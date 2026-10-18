"""Any chart a user can ask for, as one validated request compiled from fixed SQL fragments.

The model fills in an AnalysisRequest; it never writes SQL. Column names, groupings and
statistics come from the dictionaries below, chosen by validated Literal values; every
other value is a bound parameter.
"""
import re
from typing import Literal

from pydantic import Field, model_validator

from database import QueryLimitError, fetch_rows
from regions import REGIONS, RegionFilter, describe_filter, filter_dict, region_conditions

MEASURES = {  # key -> (column, label, units)
    "temp": ("temp", "Temperature", "°C"),
    "psal": ("psal", "Salinity", "PSU"),
    "doxy": ("doxy", "Oxygen", "µmol/kg"),
    "chla": ("chla", "Chlorophyll-a", "mg/m³"),
    "nitrate": ("nitrate", "Nitrate", "µmol/kg"),
    "bbp700": ("bbp700", "Backscatter 700 nm", "m⁻¹"),
    "profiles": (None, "Profiles", "profiles"),
}
STAT_LABELS = {"mean": "Mean", "median": "Median", "min": "Minimum", "max": "Maximum",
               "std": "Standard deviation of", "count": "Number of", "p10": "10th percentile of",
               "p90": "90th percentile of"}
GROUP_LABELS = {"none": "", "day": "day", "week": "week", "month": "month", "year": "year",
                "month_of_year": "calendar month", "depth": "depth", "float": "float",
                "lat_band": "latitude band", "lon_band": "longitude band"}
TIME_GROUPS = ("day", "week", "month", "year", "month_of_year")
LOW_PROFILES = 3     # fewer profiles than this behind a point means low confidence
MAX_POINTS = 1000    # per series

# group -> (SQL expression, request field sizing its bins); the only grouping SQL there is.
GROUP_SQL = {
    "none": ("'All'", None),
    "day": ("to_char(date_utc, 'YYYY-MM-DD')", None),
    "week": ("to_char(date_trunc('week', date_utc), 'YYYY-MM-DD')", None),
    "month": ("to_char(date_trunc('month', date_utc), 'YYYY-MM')", None),
    "year": ("EXTRACT(YEAR FROM date_utc)::int", None),
    "month_of_year": ("EXTRACT(MONTH FROM date_utc)::int", None),
    "depth": ("FLOOR(pres / %s) * %s", "depth_bin"),
    "float": ("wmo_id", None),
    "lat_band": ("FLOOR(latitude / %s) * %s", "band_deg"),
    "lon_band": ("FLOOR(longitude / %s) * %s", "band_deg"),
}
# Over per-profile values: v = the profile's mean in the layer, lo/hi = its raw extremes.
STAT_SQL = {
    "mean": "AVG(v)",
    "median": "percentile_cont(0.5) WITHIN GROUP (ORDER BY v)",
    "std": "STDDEV_SAMP(v)",
    "p10": "percentile_cont(0.1) WITHIN GROUP (ORDER BY v)",
    "p90": "percentile_cont(0.9) WITHIN GROUP (ORDER BY v)",
    "min": "MIN(lo)",
    "max": "MAX(hi)",
    "count": "COUNT(*)",
}
TOO_MANY = "too_many"

Measurement = Literal["temp", "psal", "doxy", "chla", "nitrate", "bbp700"]


class AnalysisRequest(RegionFilter):
    measure: Literal["temp", "psal", "doxy", "chla", "nitrate", "bbp700", "profiles"] = "temp"
    stat: Literal["mean", "median", "min", "max", "std", "count", "p10", "p90"] = "mean"
    group_by: Literal["none", "day", "week", "month", "year", "month_of_year", "depth", "float",
                      "lat_band", "lon_band"] = "month"
    compare_by: Literal["none", "region", "float", "year"] = "none"
    compare_values: list[str | int] = Field(default_factory=list, max_length=4)
    x_measure: Measurement | None = None
    chart: Literal["auto", "line", "bar", "scatter", "histogram", "profile", "map"] = "auto"
    pres_min: float | None = Field(default=None, ge=0, le=6000, allow_inf_nan=False)
    pres_max: float | None = Field(default=None, gt=0, le=6000, allow_inf_nan=False)
    depth_bin: int = Field(default=50, ge=10, le=500)
    band_deg: float = Field(default=5, ge=1, le=20, allow_inf_nan=False)
    bins: int = Field(default=30, ge=5, le=60)

    def effective_group_by(self):
        if self.chart == "profile":
            return "depth"
        if self.chart in ("scatter", "histogram", "map"):
            return "none"
        return self.group_by

    def resolved_chart(self):
        if self.chart != "auto":
            return self.chart
        if self.group_by == "depth":
            return "profile"
        return "line" if self.group_by in TIME_GROUPS else "bar"

    def layer(self):
        """The pressure range (dbar) the statistic is computed over."""
        lo = self.pres_min or 0
        if self.pres_max is not None:
            return lo, self.pres_max
        chart = self.resolved_chart()
        if chart == "profile":
            return lo, max(2000, lo + self.depth_bin)
        return (lo, 6000) if chart == "scatter" else (lo, lo + 10)

    def _compare_value(self, value):
        if self.compare_by == "region" and value in REGIONS:
            return value
        if self.compare_by == "float" and re.fullmatch(r"\d{5,8}", str(value)):
            return str(value)
        if self.compare_by == "year" and re.fullmatch(r"\d{4}", str(value)) and 1990 <= int(value) <= 2100:
            return int(value)
        raise ValueError("Unknown comparison value.")

    @model_validator(mode="after")
    def valid_combination(self):
        chart, group = self.resolved_chart(), self.effective_group_by()
        if self.measure == "profiles":
            if self.stat != "count":
                raise ValueError("Profile counts use stat count.")
            if chart in ("scatter", "histogram", "profile", "map") or group == "depth":
                raise ValueError("Profile counts can't be drawn as that chart.")
        if chart == "scatter" and (self.x_measure is None or self.x_measure == self.measure):
            raise ValueError("A scatter chart needs a different x_measure.")
        if self.x_measure is not None and chart != "scatter":
            raise ValueError("x_measure is only used by scatter charts.")
        if (self.compare_by == "none") != (not self.compare_values) or len(self.compare_values) > 4:
            raise ValueError("compare_values must list 1–4 values for compare_by.")
        self.compare_values = [self._compare_value(value) for value in self.compare_values]
        if self.compare_by == "region" and self.bounds():
            raise ValueError("Put the regions in compare_values, not region.")
        if self.compare_by == "year" and group not in ("month_of_year", "none"):
            raise ValueError("Comparing years needs group_by month_of_year or none.")
        if self.compare_by != "none" and chart in ("scatter", "histogram", "map"):
            raise ValueError("Comparisons work with line, bar and profile charts.")
        if chart == "line" and group not in TIME_GROUPS:
            raise ValueError("Line charts need a time grouping.")
        if chart == "bar" and group == "depth":
            raise ValueError("Depth grouping is drawn as a profile chart.")
        if self.pres_max is not None and self.pres_max <= (self.pres_min or 0):
            raise ValueError("pres_max must exceed pres_min.")
        return self


def _day(value):
    return str(value)[:10] if value else None


def _series_filters(req):
    """(name, filter, extra conditions, extra params) for each series the request draws."""
    base = RegionFilter(**filter_dict(req))
    if req.compare_by == "none":
        return [(describe_filter(base), base, [], [])]
    series = []
    for value in req.compare_values:
        if req.compare_by == "region":
            series.append((REGIONS[value]["label"], base.model_copy(update={"region": value}), [], []))
        elif req.compare_by == "float":
            series.append((f"WMO {value}", base.model_copy(update={"wmo_id": value}), [], []))
        else:
            series.append((str(value), base, ["EXTRACT(YEAR FROM date_utc)::int = %s"], [value]))
    return series


def _grouped_series(req, name, f, extra_conditions, extra_params):
    conditions, params = region_conditions(f)
    where = " AND ".join(conditions + extra_conditions)
    params = params + extra_params
    expr, size_field = GROUP_SQL[req.effective_group_by()]
    group_params = [getattr(req, size_field)] * 2 if size_field else []
    if req.measure == "profiles":
        rows = fetch_rows(f"""SELECT {expr} AS x, COUNT(*) AS y, COUNT(*) AS n_profiles,
            COUNT(DISTINCT wmo_id) AS n_floats FROM profile_summary WHERE {where} GROUP BY 1 ORDER BY 1""",
            group_params + params, max_rows=MAX_POINTS)
        totals = fetch_rows(f"""SELECT COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats,
            MIN(date_utc) AS first_date, MAX(date_utc) AS last_date FROM profile_summary WHERE {where}""",
            params)[0]
    else:
        column = MEASURES[req.measure][0]
        obs_where = f"{column} IS NOT NULL AND pres >= %s AND pres <= %s AND {where}"
        obs_params = list(req.layer()) + params
        rows = fetch_rows(f"""WITH per_profile AS (
                SELECT profile_id, wmo_id, {expr} AS g, AVG({column}) AS v, MIN({column}) AS lo, MAX({column}) AS hi
                FROM observations WHERE {obs_where} GROUP BY profile_id, wmo_id, 3)
            SELECT g AS x, {STAT_SQL[req.stat]} AS y, COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats
            FROM per_profile GROUP BY g ORDER BY g""", group_params + obs_params, max_rows=MAX_POINTS)
        totals = fetch_rows(f"""SELECT COUNT(DISTINCT profile_id) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats,
            MIN(date_utc) AS first_date, MAX(date_utc) AS last_date FROM observations WHERE {obs_where}""",
            obs_params)[0]
    points = [{"x": row["x"], "y": row["y"], "n_profiles": row["n_profiles"], "n_floats": row["n_floats"],
               "low": req.measure != "profiles" and row["n_profiles"] < LOW_PROFILES} for row in rows]
    return {"name": name, "points": points, "n_profiles": totals["n_profiles"], "n_floats": totals["n_floats"],
            "first_date": _day(totals["first_date"]), "last_date": _day(totals["last_date"])}


def run_analysis(**request):
    """Validate, compile and run one analysis; never raises for too much or too little data."""
    req = AnalysisRequest(**request)
    _, label, units = MEASURES[req.measure]
    group = req.effective_group_by()
    base = RegionFilter(**filter_dict(req))
    specs = _series_filters(req)
    title = f"{STAT_LABELS[req.stat]} {label.lower()}" + (f" by {GROUP_LABELS[group]}" if group != "none" else "")
    title += " · " + (" vs ".join(name for name, *_ in specs) if req.compare_by != "none" else describe_filter(base))
    result = {"status": "ok", "title": title, "measure": req.measure, "measure_label": label, "units": units,
              "stat": req.stat, "group_by": group, "chart": req.resolved_chart(), "pressure_range": list(req.layer()),
              "depth_bin": req.depth_bin, "x_measure": req.x_measure, "bins": req.bins, "series": [], "points": [],
              "diagnosis": None, "coverage": None, "notes": [], "n_profiles": 0,
              "filter": None if req.compare_by in ("region", "float") else filter_dict(base),
              "filter_label": describe_filter(base),
              "dashboard": "The dashboard charts this result and highlights the matching profiles on the map."}
    try:
        result["series"] = [_grouped_series(req, *spec) for spec in specs]
    except QueryLimitError:
        result["status"] = TOO_MANY
        return result
    points = [point for series in result["series"] for point in series["points"]]
    result["n_profiles"] = sum(series["n_profiles"] for series in result["series"])
    if any(f.region and REGIONS[f.region]["approximate"] for _, f, *_ in specs):
        result["notes"].append("Region boxes are approximate.")
    if any(point["low"] for point in points):
        result["notes"].append("Points from fewer than 3 profiles are faded (low confidence).")
    if not points:
        result["status"] = "no_data"
    elif all(point["low"] for point in points):
        result["status"] = "low_data"
    return result
