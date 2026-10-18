"""Any chart a user can ask for, as one validated request compiled from fixed SQL fragments.

The model fills in an AnalysisRequest; it never writes SQL. Column names, groupings and
statistics come from the dictionaries below, chosen by validated Literal values; every
other value is a bound parameter.
"""
import re
import time
from functools import lru_cache
from typing import Literal

import numpy
from pydantic import Field, model_validator

from database import QueryLimitError, fetch_rows
from regions import (REGIONS, RegionFilter, box_contains, describe_filter, filter_dict, region_boxes,
                     region_conditions, region_phrase)

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
MAX_ROWS = 5000          # histogram values, scatter levels, map profiles
SAMPLE_PROFILES = 300    # scatter draws its levels from this many random matching profiles

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


def _layer_where(req, f, column):
    conditions, params = region_conditions(f)
    return (f"{column} IS NOT NULL AND pres >= %s AND pres <= %s AND " + " AND ".join(conditions),
            list(req.layer()) + params)


def _histogram(req, f):
    """Counts of per-profile layer means in `bins` equal bins."""
    column = MEASURES[req.measure][0]
    where, params = _layer_where(req, f, column)
    rows = fetch_rows(f"""SELECT profile_id, wmo_id, AVG({column}) AS v FROM observations WHERE {where}
        GROUP BY profile_id, wmo_id""", params, max_rows=MAX_ROWS)
    if not rows:
        return [], 0, 0, []
    counts, edges = numpy.histogram([row["v"] for row in rows], bins=req.bins)
    points = [{"x": float((edges[i] + edges[i + 1]) / 2), "x0": float(edges[i]), "x1": float(edges[i + 1]),
               "y": int(count)} for i, count in enumerate(counts)]
    return points, len(rows), len({row["wmo_id"] for row in rows}), []


def _scatter(req, f):
    """Paired levels from a random sample of matching profiles."""
    conditions, params = region_conditions(f)
    sample = fetch_rows(f"""SELECT profile_id FROM profile_summary WHERE {" AND ".join(conditions)}
        ORDER BY random() LIMIT %s""", params + [SAMPLE_PROFILES], max_rows=SAMPLE_PROFILES)
    if not sample:
        return [], 0, 0, []
    x_column, y_column = MEASURES[req.x_measure][0], MEASURES[req.measure][0]
    # Each sampled profile gets an equal share of the level cap, at random depths, so a few dense
    # profiles can't fill the chart on their own.
    per_profile = max(1, MAX_ROWS // len(sample))
    rows = fetch_rows(f"""SELECT profile_id, wmo_id, x, y, pres FROM (
            SELECT profile_id, wmo_id, {x_column} AS x, {y_column} AS y, pres,
                ROW_NUMBER() OVER (PARTITION BY profile_id ORDER BY random()) AS rn
            FROM observations WHERE profile_id = ANY(%s) AND {x_column} IS NOT NULL AND {y_column} IS NOT NULL
            AND pres >= %s AND pres <= %s) AS levels
        WHERE rn <= %s LIMIT %s""",
        [[row["profile_id"] for row in sample], *req.layer(), per_profile, MAX_ROWS], max_rows=MAX_ROWS)
    points = [{"x": row["x"], "y": row["y"], "pres": row["pres"]} for row in rows]
    note = [f"Random levels from a random sample of up to {SAMPLE_PROFILES} profiles ({MAX_ROWS:,} levels at most)."]
    return points, len({row["profile_id"] for row in rows}), len({row["wmo_id"] for row in rows}), note


def _map(req, f):
    """Each profile's layer mean at its position, newest first."""
    column = MEASURES[req.measure][0]
    where, params = _layer_where(req, f, column)
    rows = fetch_rows(f"""SELECT profile_id, wmo_id, MIN(latitude) AS lat, MIN(longitude) AS lon,
        AVG({column}) AS value, MIN(date_utc) AS date FROM observations WHERE {where}
        GROUP BY profile_id, wmo_id ORDER BY date DESC LIMIT %s""", params + [MAX_ROWS], max_rows=MAX_ROWS)
    points = [{"lat": row["lat"], "lon": row["lon"], "value": row["value"], "profile_id": row["profile_id"],
               "wmo_id": row["wmo_id"]} for row in rows]
    note = [f"Only the latest {MAX_ROWS:,} profiles are mapped."] if len(rows) == MAX_ROWS else []
    return points, len(rows), len({row["wmo_id"] for row in rows}), note


POINT_CHARTS = {"histogram": _histogram, "scatter": _scatter, "map": _map}

SUPPORTED_SUMMARY = ("I can chart temperature, salinity, oxygen, chlorophyll-a, nitrate, backscatter or profile counts, "
                     "as a mean, median, minimum, maximum, standard deviation, count or 10th/90th percentile, by day, "
                     "week, month, year, calendar month, depth, float or latitude/longitude band, for named regions "
                     "(including approximate ocean boxes) or a latitude/longitude box, and compare up to four "
                     "regions, floats or years.")


def _plural(count, noun):
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def _coord(value, positive, negative):
    return "0.0°" if value == 0 else f"{abs(value):.1f}°{positive if value > 0 else negative}"


@lru_cache(maxsize=1)
def _coverage(bucket):
    """What the database holds; `bucket` changes every 10 minutes, which refreshes the cache."""
    row = fetch_rows("""SELECT COUNT(DISTINCT wmo_id) AS floats, COUNT(*) AS profiles, MIN(date_utc) AS first_date,
        MAX(date_utc) AS last_date, MIN(latitude) AS lat_min, MAX(latitude) AS lat_max,
        MIN(longitude) AS lon_min, MAX(longitude) AS lon_max FROM profile_summary""")[0]
    columns = [key for key, (column, *_) in MEASURES.items() if column]
    counts = fetch_rows("SELECT " + ", ".join(f"COUNT({MEASURES[key][0]}) AS {key}" for key in columns)
                        + " FROM observations")[0]
    area = None
    if row["profiles"]:
        lat = (row["lat_min"] + row["lat_max"]) / 2
        lon = (row["lon_min"] + row["lon_max"]) / 2
        for key, box in REGIONS.items():
            if box["approximate"] and any(box_contains(b, lat, lon) for b in region_boxes(key)):
                area = box["label"]
                break
    return {"floats": row["floats"], "profiles": row["profiles"], "first_date": _day(row["first_date"]),
            "last_date": _day(row["last_date"]), "lat": [row["lat_min"], row["lat_max"]],
            "lon": [row["lon_min"], row["lon_max"]], "measures": dict(counts), "area": area}


def data_coverage():
    return dict(_coverage(int(time.time() // 600)))


def coverage_sentence(cov):
    if not cov.get("profiles"):
        return "The database has no profiles yet."
    (lat0, lat1), (lon0, lon1) = cov["lat"], cov["lon"]
    box = f"{_coord(lat0, 'N', 'S')}–{_coord(lat1, 'N', 'S')}, {_coord(lon0, 'E', 'W')}–{_coord(lon1, 'E', 'W')}"
    where = f" in the {cov['area']} area ({box})" if cov.get("area") else f" ({box})"
    return f"This database has {_plural(cov['floats'], 'float')}{where}, {cov['first_date']} to {cov['last_date']}."


def _profile_count(f):
    conditions, params = region_conditions(f)
    return fetch_rows(f"SELECT COUNT(*) AS n FROM profile_summary WHERE {' AND '.join(conditions)}", params)[0]["n"]


def _has_values(column, f, layer=None):
    conditions, params = region_conditions(f)
    if layer:
        conditions, params = ["pres >= %s AND pres <= %s"] + conditions, list(layer) + params
    return fetch_rows(f"SELECT EXISTS (SELECT 1 FROM observations WHERE {column} IS NOT NULL AND "
                      f"{' AND '.join(conditions)}) AS found", params)[0]["found"]


def diagnose_empty(req, f):
    """Name the first filter that leaves nothing: area, float, dates, months, then the measurement."""
    phrase = region_phrase(f)
    subject = phrase[0].upper() + phrase[1:]
    current = RegionFilter(region=f.region, lat_min=f.lat_min, lat_max=f.lat_max, lon_min=f.lon_min, lon_max=f.lon_max)
    if not _profile_count(current):
        return f"No profiles in {phrase} at all."
    if f.wmo_id:
        current = current.model_copy(update={"wmo_id": f.wmo_id})
        if not _profile_count(current):
            return f"Float {f.wmo_id} has no profiles in {phrase}."
    if f.start_date or f.end_date:
        found = _profile_count(current)
        current = current.model_copy(update={"start_date": f.start_date, "end_date": f.end_date})
        if not _profile_count(current):
            span = (f"between {f.start_date} and {f.end_date}" if f.start_date and f.end_date
                    else f"from {f.start_date}" if f.start_date else f"until {f.end_date}")
            return f"{subject} has {found:,} profiles, but none {span}."
    if f.months:
        found = _profile_count(current)
        current = current.model_copy(update={"months": f.months})
        if not _profile_count(current):
            return f"{subject} has {found:,} profiles, but none in {describe_filter(RegionFilter(months=f.months))}."
    if req.measure != "profiles":
        column, label, _ = MEASURES[req.measure]
        if not _has_values(column, current):
            return f"These profiles have no {label.lower()} measurements."
        if not _has_values(column, current, req.layer()):
            lo, hi = req.layer()
            return f"These profiles have {label.lower()} measurements, but none between {lo:g} and {hi:g} dbar."
    return "No measurements match this request."


def _title(req, specs, base):
    label = MEASURES[req.measure][1]
    chart, group = req.resolved_chart(), req.effective_group_by()
    if chart == "histogram":
        title = f"Distribution of profile-mean {label.lower()}"
    elif chart == "scatter":
        title = f"{label} vs {MEASURES[req.x_measure][1].lower()}"
    elif chart == "map":
        title = f"{label} by location"
    else:
        title = f"{STAT_LABELS[req.stat]} {label.lower()}" + (f" by {GROUP_LABELS[group]}" if group != "none" else "")
    return title + " · " + (" vs ".join(name for name, *_ in specs) if req.compare_by != "none" else describe_filter(base))


def run_analysis(**request):
    """Validate, compile and run one analysis; never raises for too much or too little data."""
    req = AnalysisRequest(**request)
    _, label, units = MEASURES[req.measure]
    group = req.effective_group_by()
    base = RegionFilter(**filter_dict(req))
    specs = _series_filters(req)
    result = {"status": "ok", "title": _title(req, specs, base), "measure": req.measure, "measure_label": label, "units": units,
              "stat": req.stat, "group_by": group, "chart": req.resolved_chart(), "pressure_range": list(req.layer()),
              "depth_bin": req.depth_bin, "x_measure": req.x_measure, "bins": req.bins, "series": [], "points": [],
              "diagnosis": None, "coverage": None, "notes": [], "n_profiles": 0, "n_floats": 0,
              "filter": None if req.compare_by in ("region", "float") else filter_dict(base),
              "filter_label": describe_filter(base),
              "dashboard": "The dashboard charts this result and highlights the matching profiles on the map."}
    if any(f.region and REGIONS[f.region]["approximate"] for _, f, *_ in specs):
        result["notes"].append("Region boxes are approximate.")
    try:
        if result["chart"] in POINT_CHARTS:   # never compared (validated), so one filter
            points, result["n_profiles"], result["n_floats"], notes = POINT_CHARTS[result["chart"]](req, base)
            result["points"] = points
            result["notes"] += notes
            low = 0 < result["n_profiles"] < LOW_PROFILES
        else:
            result["series"] = [_grouped_series(req, *spec) for spec in specs]
            points = [point for series in result["series"] for point in series["points"]]
            result["n_profiles"] = sum(series["n_profiles"] for series in result["series"])
            result["n_floats"] = max((series["n_floats"] for series in result["series"]), default=0)
            low = bool(points) and all(point["low"] for point in points)
            if any(point["low"] for point in points):
                result["notes"].append("Points from fewer than 3 profiles are faded (low confidence).")
    except QueryLimitError:
        result.update(status=TOO_MANY, series=[], points=[])
        return result
    if not points:
        result["status"] = "no_data"
        result["coverage"] = data_coverage()
        sentences = []
        for _, f, *_ in specs:
            sentence = diagnose_empty(req, f)
            if sentence not in sentences:
                sentences.append(sentence)
        result["diagnosis"] = " ".join(sentences + [coverage_sentence(result["coverage"])])
    elif low:
        result["status"] = "low_data"
    return result
