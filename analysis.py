"""Any chart a user can ask for, as one validated request compiled from fixed SQL fragments.

The model fills in an AnalysisRequest; it never writes SQL. Column names, groupings and
statistics come from the dictionaries below, chosen by validated Literal values; every
other value is a bound parameter.
"""
import re
from typing import Literal

from pydantic import Field, model_validator

from regions import REGIONS, RegionFilter

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
