"""Validated, bounded data operations. Never execute model-generated SQL."""
from datetime import date, timedelta
from typing import Literal
from pydantic import Field, model_validator
from analysis import SUPPORTED_SUMMARY, AnalysisRequest, data_coverage, run_analysis
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


class UnsupportedArgs(Arguments):
    reason: str = Field(min_length=1, max_length=300)


def unsupported(reason):
    """An explicit, honest refusal the planner chooses when no operation fits."""
    return {"status": "unsupported", "reason": reason, "supported": SUPPORTED_SUMMARY, "chart": "none", "series": []}


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
    "analyze": (AnalysisRequest, run_analysis,
                "Compute and chart any statistic of a measurement over matching profiles: measure, stat, group_by, "
                "compare_by/compare_values, filters, chart. Use for averages, trends, distributions, comparisons "
                "and any graph."),
    "data_coverage": (SummaryArgs, data_coverage,
                      "What the database covers: floats, dates, area and how many values each measurement has."),
    "unsupported": (UnsupportedArgs, unsupported,
                    "Use when no operation can answer the question (derived quantities, forecasts, data outside "
                    "this database)."),
}


class ToolPlan(Arguments):
    tool: Literal["dataset_summary", "list_floats", "search_profiles", "profile_data", "trajectory",
                  "region_profiles", "analyze", "data_coverage", "unsupported"]
    arguments: dict = Field(default_factory=dict)


def execute_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown data operation.")
    model, function, _ = TOOLS[name]
    return function(**model.model_validate(arguments).model_dump())


def tool_catalog():
    return {name: {"description": description, "parameters": model.model_json_schema()}
            for name, (model, _, description) in TOOLS.items()}
