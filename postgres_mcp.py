"""
FloatChat — PostgreSQL MCP Tool Server
=======================================
Exposes structured tools for the LangChain agent to query ARGO data.

Tools:
  query_data           — run arbitrary SELECT SQL
  get_nearby_floats    — find floats near a lat/lon within a radius
  get_float_trajectory — full lat/lon track for a WMO float
  get_profile_data     — T/S/P depth levels for a specific profile
  get_bgc_profile      — BGC variables for a specific profile
  list_floats          — all known WMO floats in the DB
  get_date_range_profiles — profiles between two dates, optionally filtered by region

Run via MCP stdio transport — called by app.py's StdioServerParameters.
"""

import json
import os
import math
import psycopg2
import psycopg2.extras
from loguru import logger
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("FloatChat_Postgres")

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME",  "FloatChat"),
    "user":     os.getenv("DB_USER",  "postgres"),
    "password": os.getenv("DB_PASS",  "12345"),
    "host":     os.getenv("DB_HOST",  "localhost"),
    "port":     os.getenv("DB_PORT",  "5432"),
}


# ── Connection helper ──────────────────────────────────────

def _connect():
    return psycopg2.connect(**DB_CONFIG)


def _run_query(sql: str, params=None) -> list[dict]:
    """Execute a SELECT and return rows as a list of dicts."""
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Tools ─────────────────────────────────────────────────

@mcp.tool()
def query_data(sql_query: str) -> str:
    """
    Execute any read-only SQL SELECT against the ARGO database and return results as JSON.
    Tables available: floats, profiles, observations, profile_summary (view).
    ONLY SELECT statements are allowed.
    """
    sql_upper = sql_query.strip().upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
        return json.dumps({"error": "Only SELECT / WITH queries are permitted."})

    logger.info("query_data: {}", sql_query[:200])
    try:
        rows = _run_query(sql_query)
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        logger.error("query_data error: {}", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_nearby_floats(latitude: float, longitude: float, radius_km: float = 500.0,
                      start_date: str = None, end_date: str = None) -> str:
    """
    Find ARGO float profiles within radius_km kilometres of the given lat/lon.
    Optionally filter by date range (ISO format: YYYY-MM-DD).
    Returns profile summaries sorted by distance.
    """
    logger.info("get_nearby_floats: lat={} lon={} r={}km", latitude, longitude, radius_km)

    # Approximate degree-to-km: 1° ≈ 111 km
    deg_radius = radius_km / 111.0

    date_filter = ""
    params = [latitude, longitude, latitude - deg_radius, latitude + deg_radius,
              longitude - deg_radius, longitude + deg_radius]

    if start_date:
        date_filter += " AND date_utc >= %s"
        params.append(start_date)
    if end_date:
        date_filter += " AND date_utc <= %s"
        params.append(end_date)

    sql = f"""
        SELECT
            profile_id,
            wmo_id,
            cycle_number,
            date_utc,
            latitude,
            longitude,
            data_mode,
            ROUND(
                111.0 * SQRT(
                    POWER(latitude  - %s, 2) +
                    POWER(longitude - %s, 2) * POWER(COS(RADIANS(%s)), 2)
                )::NUMERIC, 1
            ) AS distance_km
        FROM profiles
        WHERE
            latitude  BETWEEN %s AND %s
            AND longitude BETWEEN %s AND %s
            AND latitude  IS NOT NULL
            AND longitude IS NOT NULL
            {date_filter}
        ORDER BY distance_km ASC
        LIMIT 50
    """
    # Rebuild params in correct order
    params = [latitude, longitude, latitude,
              latitude - deg_radius, latitude + deg_radius,
              longitude - deg_radius, longitude + deg_radius]
    if start_date:
        params.append(start_date)
    if end_date:
        params.append(end_date)

    try:
        rows = _run_query(sql, params)
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        logger.error("get_nearby_floats error: {}", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_float_trajectory(wmo_id: str, start_date: str = None, end_date: str = None) -> str:
    """
    Return the full trajectory (ordered lat/lon positions) for a given WMO float ID.
    Useful for plotting the float's track on a map.
    """
    logger.info("get_float_trajectory: wmo={}", wmo_id)
    date_filter = ""
    params = [wmo_id]
    if start_date:
        date_filter += " AND date_utc >= %s"
        params.append(start_date)
    if end_date:
        date_filter += " AND date_utc <= %s"
        params.append(end_date)

    sql = f"""
        SELECT cycle_number, date_utc, latitude, longitude, data_mode
        FROM profiles
        WHERE wmo_id = %s {date_filter}
          AND latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY date_utc ASC
    """
    try:
        rows = _run_query(sql, params)
        return json.dumps({"wmo_id": wmo_id, "trajectory": rows}, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_profile_data(profile_id: int) -> str:
    """
    Return temperature, salinity, and pressure depth levels for a specific profile_id.
    Useful for plotting a T/S profile or depth vs temperature chart.
    """
    logger.info("get_profile_data: profile_id={}", profile_id)
    sql = """
        SELECT
            o.depth_level, o.pres, o.pres_qc,
            o.temp, o.temp_qc,
            o.psal, o.psal_qc,
            p.date_utc, p.latitude, p.longitude, p.wmo_id
        FROM observations o
        JOIN profiles p ON p.profile_id = o.profile_id
        WHERE o.profile_id = %s
        ORDER BY o.pres ASC
    """
    try:
        rows = _run_query(sql, [profile_id])
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_bgc_profile(profile_id: int) -> str:
    """
    Return BGC parameters (dissolved oxygen, chlorophyll-a, nitrate, backscatter)
    for a specific profile_id. Returns only levels where at least one BGC value exists.
    """
    logger.info("get_bgc_profile: profile_id={}", profile_id)
    sql = """
        SELECT
            depth_level, pres,
            doxy,    doxy_qc,
            chla,    chla_qc,
            nitrate, nitrate_qc,
            bbp700,  bbp700_qc
        FROM observations
        WHERE profile_id = %s
          AND (doxy IS NOT NULL OR chla IS NOT NULL
               OR nitrate IS NOT NULL OR bbp700 IS NOT NULL)
        ORDER BY pres ASC
    """
    try:
        rows = _run_query(sql, [profile_id])
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_floats(limit: int = 100) -> str:
    """
    Return a list of all ARGO floats loaded into the database, with their
    DAC, project name, and profile count.
    """
    logger.info("list_floats")
    sql = """
        SELECT
            f.wmo_id, f.platform_type, f.dac, f.project_name, f.pi_name,
            COUNT(p.profile_id) AS n_profiles,
            MIN(p.date_utc)     AS first_profile,
            MAX(p.date_utc)     AS last_profile
        FROM floats f
        LEFT JOIN profiles p ON p.wmo_id = f.wmo_id
        GROUP BY f.wmo_id, f.platform_type, f.dac, f.project_name, f.pi_name
        ORDER BY n_profiles DESC
        LIMIT %s
    """
    try:
        rows = _run_query(sql, [limit])
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_date_range_profiles(start_date: str, end_date: str,
                             lat_min: float = None, lat_max: float = None,
                             lon_min: float = None, lon_max: float = None,
                             limit: int = 200) -> str:
    """
    Return profiles within a date range, with optional bounding-box filter.
    Dates in ISO format: YYYY-MM-DD.
    Example: start_date='2023-01-01', end_date='2023-03-31', lat_min=0, lat_max=30
    """
    logger.info("get_date_range_profiles: {} to {}", start_date, end_date)
    bbox_filter = ""
    params = [start_date, end_date]

    if lat_min is not None:
        bbox_filter += " AND latitude >= %s"
        params.append(lat_min)
    if lat_max is not None:
        bbox_filter += " AND latitude <= %s"
        params.append(lat_max)
    if lon_min is not None:
        bbox_filter += " AND longitude >= %s"
        params.append(lon_min)
    if lon_max is not None:
        bbox_filter += " AND longitude <= %s"
        params.append(lon_max)

    params.append(limit)

    sql = f"""
        SELECT
            profile_id, wmo_id, cycle_number, date_utc,
            latitude, longitude, data_mode, n_levels,
            pres_max, temp_min, temp_max, psal_min, psal_max,
            has_doxy, has_chla
        FROM profile_summary
        WHERE date_utc BETWEEN %s AND %s {bbox_filter}
        ORDER BY date_utc DESC
        LIMIT %s
    """
    try:
        rows = _run_query(sql, params)
        return json.dumps(rows, default=str, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Run ───────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting FloatChat MCP server...")
    mcp.run(transport="stdio")
