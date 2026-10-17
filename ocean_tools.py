"""Validated, bounded data operations. Never execute model-generated SQL."""
from datetime import date, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from database import fetch_rows


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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
}


class ToolPlan(Arguments):
    tool: Literal["dataset_summary", "list_floats", "search_profiles", "profile_data", "trajectory"]
    arguments: dict = Field(default_factory=dict)


def execute_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown data operation.")
    model, function, _ = TOOLS[name]
    return function(**model.model_validate(arguments).model_dump())


def tool_catalog():
    return {name: {"description": description, "parameters": model.model_json_schema()}
            for name, (model, _, description) in TOOLS.items()}
