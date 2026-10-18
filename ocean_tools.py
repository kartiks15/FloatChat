"""Validated, bounded data operations. Never execute model-generated SQL."""
from datetime import date, timedelta
from typing import Literal
from pydantic import Field, model_validator
from database import fetch_rows
from regions import (Arguments, MONTH_NAMES, REGIONS, RegionFilter, describe_filter, filter_dict,  # noqa: F401
                     region_conditions)


class SummaryArgs(Arguments):
    pass


class FloatArgs(Arguments):
    wmo_id: str = Field(pattern=r"^\d{5,8}$")


class ProfileArgs(Arguments):
    profile_id: int = Field(gt=0, le=2147483647)


class ListArgs(Arguments):
    dac: str | None = Field(default=None, max_length=64)


class SearchArgs(Arguments):
    start_date: date | None = None
    end_date: date | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    radius_km: float = Field(default=500, gt=0, le=20000, allow_inf_nan=False)
    wmo_id: str | None = Field(default=None, pattern=r"^\d{5,8}$")
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def valid_range(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude.")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must precede end_date.")
        if self.end_date == date.max:
            raise ValueError("end_date is out of range.")
        return self


def dataset_summary():
    return fetch_rows("""SELECT (SELECT COUNT(*) FROM floats) AS floats,
        COUNT(*) AS profiles, MIN(date_utc) AS first_date, MAX(date_utc) AS last_date,
        (SELECT COUNT(*) FROM observations) AS observations FROM profiles""")[0]


def list_floats(dac=None):
    rows = fetch_rows("""SELECT f.wmo_id, f.dac, COUNT(p.profile_id) AS n_profiles,
        MIN(p.date_utc) AS first_date, MAX(p.date_utc) AS last_date
        FROM floats f LEFT JOIN profiles p ON p.wmo_id=f.wmo_id
        WHERE (%s IS NULL OR LOWER(f.dac)=LOWER(%s))
        GROUP BY f.wmo_id, f.dac ORDER BY n_profiles DESC, f.wmo_id LIMIT 101""", [dac, dac])
    return {"rows": rows[:100], "truncated": len(rows) > 100}


def search_profiles(**kwargs):
    args = SearchArgs(**kwargs)
    conditions = ["latitude BETWEEN -90 AND 90", "longitude BETWEEN -180 AND 180"]
    params = []
    for name, op, val in [("date_utc", ">=", args.start_date),
                          ("date_utc", "<", args.end_date + timedelta(days=1) if args.end_date else None),
                          ("wmo_id", "=", args.wmo_id)]:
        if val is not None:
            conditions.append(f"{name} {op} %s")
            params.append(val)
    distance = "NULL::double precision"
    if args.latitude is not None:
        distance = """6371.0 * 2 * ASIN(SQRT(LEAST(1.0, GREATEST(0.0,
            POWER(SIN(RADIANS(latitude - %s)/2),2) +
            COS(RADIANS(%s))*COS(RADIANS(latitude))*
            POWER(SIN(RADIANS(longitude - %s)/2),2)))))"""
        params = [args.latitude, args.latitude, args.longitude] + params
    sql = f"""WITH candidates AS (SELECT *, {distance} AS distance_km
        FROM profile_summary WHERE {' AND '.join(conditions)}) SELECT * FROM candidates"""
    if args.latitude is not None:
        sql += " WHERE distance_km <= %s ORDER BY distance_km, profile_id"
        params.append(args.radius_km)
    else:
        sql += " ORDER BY date_utc DESC NULLS LAST, profile_id DESC"
    sql += " LIMIT %s"
    params.append(args.limit + 1)
    rows = fetch_rows(sql, params)
    return {"rows": rows[:args.limit], "truncated": len(rows) > args.limit}


def _day(value):
    return str(value)[:10] if value else None


def region_profiles(**kwargs):
    """Counts and floats for every profile in the filter; the dashboard highlights them."""
    f = RegionFilter(**kwargs)
    conditions, params = region_conditions(f)
    where = " AND ".join(conditions)
    totals = fetch_rows(f"""SELECT COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats,
        MIN(date_utc) AS first_date, MAX(date_utc) AS last_date FROM profile_summary WHERE {where}""", params)[0]
    floats = fetch_rows(f"""SELECT wmo_id, COUNT(*) AS n_profiles, MIN(date_utc) AS first_date,
        MAX(date_utc) AS last_date FROM profile_summary WHERE {where}
        GROUP BY wmo_id ORDER BY n_profiles DESC, wmo_id LIMIT 101""", params)
    return {"n_profiles": totals["n_profiles"], "n_floats": totals["n_floats"],
            "first_date": _day(totals["first_date"]), "last_date": _day(totals["last_date"]),
            "floats": [{"wmo_id": r["wmo_id"], "n_profiles": r["n_profiles"], "first_date": _day(r["first_date"]),
                        "last_date": _day(r["last_date"])} for r in floats[:100]],
            "truncated": len(floats) > 100, "filter": filter_dict(f), "label": describe_filter(f),
            "dashboard": "The map highlights these profiles."}


def region_points(**kwargs):
    """Map positions for a filter, newest first; not a model tool."""
    conditions, params = region_conditions(RegionFilter(**kwargs))
    rows = fetch_rows(f"""SELECT profile_id, wmo_id, latitude, longitude, date_utc FROM profile_summary
        WHERE {' AND '.join(conditions)} ORDER BY date_utc DESC NULLS LAST, profile_id DESC LIMIT 5000""",
        params, max_rows=5000)
    return {"rows": [dict(row, date_utc=_day(row["date_utc"])) for row in rows], "truncated": len(rows) == 5000}


VARIABLES = {  # key -> (column, label, units); the only column names that reach SQL
    "temp": ("temp", "Temperature", "°C"),
    "psal": ("psal", "Salinity", "PSU"),
    "doxy": ("doxy", "Oxygen", "µmol/kg"),
    "chla": ("chla", "Chlorophyll-a", "mg/m³"),
    "nitrate": ("nitrate", "Nitrate", "µmol/kg"),
    "bbp700": ("bbp700", "Backscatter 700 nm", "m⁻¹"),
}
BUCKETS = {
    "week": "to_char(date_trunc('week', date_utc), 'YYYY-MM-DD')",
    "month": "to_char(date_trunc('month', date_utc), 'YYYY-MM')",
    "month_of_year": "EXTRACT(MONTH FROM date_utc)::int",
    "pressure": "FLOOR(pres / %s) * %s",
}


class AggregateArgs(RegionFilter):
    variable: Literal["temp", "psal", "doxy", "chla", "nitrate", "bbp700"] = "temp"
    group_by: Literal["week", "month", "month_of_year", "pressure"] = "month"
    pres_min: float = Field(default=0, ge=0, le=6000, allow_inf_nan=False)
    pres_max: float | None = Field(default=None, gt=0, le=6000, allow_inf_nan=False)
    bin_dbar: int = Field(default=50, ge=10, le=500)

    @model_validator(mode="after")
    def valid_layer(self):
        if self.pres_max is not None and self.pres_max <= self.pres_min:
            raise ValueError("pres_max must exceed pres_min.")
        return self


def aggregate_measurements(**kwargs):
    """Mean of per-profile means, so profiles with many levels (or two grids) count once."""
    args = AggregateArgs(**kwargs)
    column, label, units = VARIABLES[args.variable]
    pres_max = args.pres_max
    if pres_max is None:  # a surface layer over time; the whole column for a depth profile
        pres_max = max(2000, args.pres_min + args.bin_dbar) if args.group_by == "pressure" else args.pres_min + 10
    conditions, region_params = region_conditions(args)
    where = " AND ".join([f"{column} IS NOT NULL", "pres >= %s", "pres <= %s", *conditions])
    params = [args.pres_min, pres_max] + region_params
    bucket_params = [args.bin_dbar, args.bin_dbar] if args.group_by == "pressure" else []
    rows = fetch_rows(f"""WITH per_profile AS (
            SELECT profile_id, wmo_id, {BUCKETS[args.group_by]} AS bucket, AVG({column}) AS value
            FROM observations WHERE {where} GROUP BY profile_id, wmo_id, 3)
        SELECT bucket, AVG(value) AS mean, STDDEV_SAMP(value) AS std, MIN(value) AS min, MAX(value) AS max,
            COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats
        FROM per_profile GROUP BY bucket ORDER BY bucket""", bucket_params + params, max_rows=1000)
    totals = fetch_rows(f"""SELECT COUNT(DISTINCT profile_id) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats,
        MIN(date_utc) AS first_date, MAX(date_utc) AS last_date FROM observations WHERE {where}""", params)[0]
    return {"variable": args.variable, "label": label, "units": units, "group_by": args.group_by,
            "pressure_range": [args.pres_min, pres_max], "bin_dbar": args.bin_dbar,
            "rows": [dict(row) for row in rows], "n_profiles": totals["n_profiles"], "n_floats": totals["n_floats"],
            "first_date": _day(totals["first_date"]), "last_date": _day(totals["last_date"]),
            "filter": filter_dict(args), "filter_label": describe_filter(args),
            "dashboard": "The dashboard charts these values and highlights the profiles on the map."}


def profile_data(profile_id):
    meta = fetch_rows("SELECT * FROM profile_summary WHERE profile_id=%s", [profile_id])
    rows = fetch_rows("""SELECT pres, temp, psal, doxy, chla, nitrate, bbp700,
        source_file, source_profile FROM observations WHERE profile_id=%s
        ORDER BY source_file, source_profile, pres LIMIT 1001""", [profile_id], max_rows=1001)
    return {"profile": meta[0] if meta else None, "rows": rows[:1000], "truncated": len(rows) > 1000}


def trajectory(wmo_id):
    rows = fetch_rows("""SELECT profile_id, wmo_id, cycle_number, date_utc, latitude, longitude
        FROM profiles WHERE wmo_id=%s AND latitude BETWEEN -90 AND 90
        AND longitude BETWEEN -180 AND 180 ORDER BY date_utc, profile_id LIMIT 1001""",
        [wmo_id], max_rows=1001)
    return {"rows": rows[:1000], "truncated": len(rows) > 1000}


TOOLS = {
    "dataset_summary": (SummaryArgs, dataset_summary, "Dataset totals and available date range."),
    "list_floats": (ListArgs, list_floats, "List floats, optionally by DAC code (INCOIS files often use IN)."),
    "search_profiles": (SearchArgs, search_profiles, "Find profiles by inclusive dates, float, or distance from a coordinate."),
    "profile_data": (ProfileArgs, profile_data, "Measurements at pressure levels for a profile ID, with provenance."),
    "trajectory": (FloatArgs, trajectory, "Locations and dates for a float's trajectory."),
    "region_profiles": (RegionFilter, region_profiles,
                        "Count, list and highlight every profile inside a named region (equator, arabian_sea, "
                        "bay_of_bengal) or a latitude/longitude box, optionally by dates, calendar months (1–12) "
                        "or float."),
    "aggregate_measurements": (AggregateArgs, aggregate_measurements,
                               "Average a variable (default temperature) over every profile matching a region, "
                               "dates, calendar months or float. group_by week, month, month_of_year (seasonal, "
                               "across years) or pressure (mean depth profile). Default layer 0–10 dbar. Use for "
                               "averages, trends and graphs."),
}


class ToolPlan(Arguments):
    tool: Literal["dataset_summary", "list_floats", "search_profiles", "profile_data", "trajectory",
                  "region_profiles", "aggregate_measurements"]
    arguments: dict = Field(default_factory=dict)


def execute_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown data operation.")
    model, function, _ = TOOLS[name]
    return function(**model.model_validate(arguments).model_dump())


def tool_catalog():
    return {name: {"description": description, "parameters": model.model_json_schema()}
            for name, (model, _, description) in TOOLS.items()}
