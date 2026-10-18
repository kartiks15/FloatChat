"""Named regions and the shared latitude/longitude, date, month and float filter."""
from datetime import date, timedelta
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# Named areas the model (or the Regions tab) may pick instead of inventing coordinates.
# Ocean boxes are rough rectangles, so they are always labelled approximate.
REGIONS = {
    "equator": dict(lat_min=-5, lat_max=5, lon_min=-180, lon_max=180, label="Equator",
                    phrase="the equatorial band (5°S–5°N)", approximate=False),
    "arabian_sea": dict(lat_min=0, lat_max=25, lon_min=50, lon_max=78, label="Arabian Sea",
                        phrase="the Arabian Sea", approximate=False),
    "bay_of_bengal": dict(lat_min=0, lat_max=23, lon_min=78, lon_max=100, label="Bay of Bengal",
                          phrase="the Bay of Bengal", approximate=False),
    "indian_ocean": dict(lat_min=-60, lat_max=30, lon_min=20, lon_max=120, label="Indian Ocean",
                         phrase="the Indian Ocean (approximate box)", approximate=True),
    "pacific_ocean": dict(lat_min=-60, lat_max=60, lon_min=120, lon_max=-70, label="Pacific Ocean",
                          phrase="the Pacific Ocean (approximate box)", approximate=True),
    "atlantic_ocean": dict(lat_min=-60, lat_max=65, lon_min=-70, lon_max=20, label="Atlantic Ocean",
                           phrase="the Atlantic Ocean (approximate box)", approximate=True),
    "southern_ocean": dict(lat_min=-90, lat_max=-60, lon_min=-180, lon_max=180, label="Southern Ocean",
                           phrase="the Southern Ocean (approximate box)", approximate=True),
    "arctic_ocean": dict(lat_min=66, lat_max=90, lon_min=-180, lon_max=180, label="Arctic Ocean",
                         phrase="the Arctic Ocean (approximate box)", approximate=True),
}
RegionName = Literal["equator", "arabian_sea", "bay_of_bengal", "indian_ocean", "pacific_ocean",
                     "atlantic_ocean", "southern_ocean", "arctic_ocean"]
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class RegionFilter(Arguments):
    """A latitude/longitude box (or named region), dates, calendar months and float."""
    region: RegionName | None = None
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
    if f.region and REGIONS[f.region]["approximate"]:
        parts.append(f"{REGIONS[f.region]['label']} (approx.)")
    elif bounds:
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


def region_phrase(f):
    """How sentences name the filter's area: 'the Arabian Sea', 'the box 0°–10°N, …', 'the database'."""
    if f.region:
        return REGIONS[f.region]["phrase"]
    if f.bounds():
        return "the box " + describe_filter(RegionFilter(lat_min=f.lat_min, lat_max=f.lat_max,
                                                         lon_min=f.lon_min, lon_max=f.lon_max))
    return "the database"


def filter_dict(f):
    """JSON-safe filter fields that re-validate into the same RegionFilter."""
    return f.model_dump(mode="json", exclude_none=True, include=set(RegionFilter.model_fields))
