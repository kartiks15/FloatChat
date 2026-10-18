"""Validated, bounded data operations. Never execute model-generated SQL."""
from datetime import date, timedelta
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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


# Named areas the model (or the Regions tab) may pick instead of inventing coordinates.
REGIONS = {
    "equator": {"lat_min": -5, "lat_max": 5, "lon_min": -180, "lon_max": 180},
    "arabian_sea": {"lat_min": 0, "lat_max": 25, "lon_min": 50, "lon_max": 78},
    "bay_of_bengal": {"lat_min": 0, "lat_max": 23, "lon_min": 78, "lon_max": 100},
}
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class RegionFilter(Arguments):
    """A latitude/longitude box (or named region), dates, calendar months and float."""
    region: Literal["equator", "arabian_sea", "bay_of_bengal"] | None = None
    lat_min: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lat_max: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon_min: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    lon_max: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    start_date: date | None = None
    end_date: date | None = None
    months: list[Annotated[int, Field(ge=1, le=12)]] | None = Field(default=None, max_length=12)
    wmo_id: str | None = Field(default=None, pattern=r"^\d{5,8}$")

    @field_validator("months")
    @classmethod
    def unique_months(cls, months):
        return sorted(set(months)) if months else None

    @model_validator(mode="after")
    def valid_filter(self):
        if (self.lat_min is None) != (self.lat_max is None) or (self.lon_min is None) != (self.lon_max is None):
            raise ValueError("Provide bounds in pairs: lat_min with lat_max, lon_min with lon_max.")
        if self.region and (self.lat_min is not None or self.lon_min is not None):
            raise ValueError("Use region or explicit bounds, not both.")
        if self.lat_min is not None and self.lat_min > self.lat_max:
            raise ValueError("lat_min must not exceed lat_max.")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must precede end_date.")
        if self.end_date == date.max:
            raise ValueError("end_date is out of range.")
        return self

    def bounds(self):
        """(lat_min, lat_max, lon_min, lon_max), or None when the filter has no area."""
        if self.region:
            box = REGIONS[self.region]
            return box["lat_min"], box["lat_max"], box["lon_min"], box["lon_max"]
        if self.lat_min is None and self.lon_min is None:
            return None
        lat = (self.lat_min, self.lat_max) if self.lat_min is not None else (-90, 90)
        lon = (self.lon_min, self.lon_max) if self.lon_min is not None else (-180, 180)
        return (*lat, *lon)


def region_conditions(f):
    """SQL conditions and bound parameters for columns shared by profile_summary and observations."""
    conditions = ["latitude BETWEEN -90 AND 90", "longitude BETWEEN -180 AND 180"]
    params = []
    bounds = f.bounds()
    if bounds:
        lat_min, lat_max, lon_min, lon_max = bounds
        if (lat_min, lat_max) != (-90, 90):
            conditions.append("latitude BETWEEN %s AND %s")
            params += [lat_min, lat_max]
        if (lon_min, lon_max) != (-180, 180):
            # A box whose western edge is east of its eastern edge crosses the ±180° line.
            conditions.append("longitude BETWEEN %s AND %s" if lon_min <= lon_max
                              else "(longitude >= %s OR longitude <= %s)")
            params += [lon_min, lon_max]
    if f.start_date:
        conditions.append("date_utc >= %s")
        params.append(f.start_date)
    if f.end_date:
        conditions.append("date_utc < %s")
        params.append(f.end_date + timedelta(days=1))
    if f.months:
        conditions.append("EXTRACT(MONTH FROM date_utc)::int = ANY(%s)")
        params.append(list(f.months))
    if f.wmo_id:
        conditions.append("wmo_id = %s")
        params.append(f.wmo_id)
    return conditions, params


def _degrees(value, positive, negative):
    return "0°" if value == 0 else f"{abs(value):g}°{positive if value > 0 else negative}"


def _months_label(months):
    if months == list(range(months[0], months[-1] + 1)) and len(months) > 1:
        return f"{MONTH_NAMES[months[0] - 1]}–{MONTH_NAMES[months[-1] - 1]}"
    return ", ".join(MONTH_NAMES[m - 1] for m in months)


def describe_filter(f):
    """A short label for the map and chart titles, e.g. '5°S–5°N · Mar–Jun'."""
    parts = []
    bounds = f.bounds()
    if bounds:
        lat_min, lat_max, lon_min, lon_max = bounds
        area = []
        if (lat_min, lat_max) != (-90, 90):
            area.append(f"{_degrees(lat_min, 'N', 'S')}–{_degrees(lat_max, 'N', 'S')}")
        if (lon_min, lon_max) != (-180, 180):
            area.append(f"{_degrees(lon_min, 'E', 'W')}–{_degrees(lon_max, 'E', 'W')}")
        if area:
            parts.append(", ".join(area))
    if f.months:
        parts.append(_months_label(f.months))
    if f.start_date and f.end_date:
        parts.append(f"{f.start_date} to {f.end_date}")
    elif f.start_date:
        parts.append(f"from {f.start_date}")
    elif f.end_date:
        parts.append(f"until {f.end_date}")
    if f.wmo_id:
        parts.append(f"WMO {f.wmo_id}")
    return " · ".join(parts) or "All profiles"


def filter_dict(f):
    """JSON-safe filter fields that re-validate into the same RegionFilter."""
    return f.model_dump(mode="json", exclude_none=True, include=set(RegionFilter.model_fields))


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
}


class ToolPlan(Arguments):
    tool: Literal["dataset_summary", "list_floats", "search_profiles", "profile_data", "trajectory",
                  "region_profiles"]
    arguments: dict = Field(default_factory=dict)


def execute_tool(name, arguments):
    if name not in TOOLS:
        raise ValueError("Unknown data operation.")
    model, function, _ = TOOLS[name]
    return function(**model.model_validate(arguments).model_dump())


def tool_catalog():
    return {name: {"description": description, "parameters": model.model_json_schema()}
            for name, (model, _, description) in TOOLS.items()}
