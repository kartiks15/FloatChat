"""Optional MCP stdio interface to FloatChat's read-only structured tools."""
import json
from mcp.server.fastmcp import FastMCP
from ocean_tools import execute_tool

mcp = FastMCP("FloatChat_Postgres")


@mcp.tool()
def get_dataset_summary() -> str:
    """Return dataset counts and date coverage."""
    return json.dumps(execute_tool("dataset_summary", {}), default=str)


@mcp.tool()
def list_floats(dac: str | None = None) -> str:
    """List floats, optionally filtering on the data assembly centre code."""
    return json.dumps(execute_tool("list_floats", {"dac": dac}), default=str)


@mcp.tool()
def search_profiles(start_date: str | None = None, end_date: str | None = None,
                    latitude: float | None = None, longitude: float | None = None,
                    radius_km: float = 500, wmo_id: str | None = None, limit: int = 30) -> str:
    """Find profiles by inclusive ISO dates, WMO, or great-circle distance."""
    return json.dumps(execute_tool("search_profiles", locals()), default=str)


@mcp.tool()
def get_profile_data(profile_id: int) -> str:
    """Return bounded measurements and metadata for a profile."""
    return json.dumps(execute_tool("profile_data", {"profile_id": profile_id}), default=str)


@mcp.tool()
def get_float_trajectory(wmo_id: str) -> str:
    """Return bounded positions for one float."""
    return json.dumps(execute_tool("trajectory", {"wmo_id": wmo_id}), default=str)


def _given(arguments):
    return {name: value for name, value in arguments.items() if value is not None}


@mcp.tool()
def find_region_profiles(region: str | None = None, lat_min: float | None = None, lat_max: float | None = None,
                         lon_min: float | None = None, lon_max: float | None = None,
                         start_date: str | None = None, end_date: str | None = None,
                         months: list[int] | None = None, wmo_id: str | None = None) -> str:
    """Count and list every profile in a named region (equator, arabian_sea, bay_of_bengal) or a box."""
    return json.dumps(execute_tool("region_profiles", _given(locals())), default=str)


@mcp.tool()
def analyze(measure: str = "temp", stat: str = "mean", group_by: str = "month", compare_by: str = "none",
            compare_values: list[str] | None = None, x_measure: str | None = None, chart: str = "auto",
            region: str | None = None, lat_min: float | None = None, lat_max: float | None = None,
            lon_min: float | None = None, lon_max: float | None = None, start_date: str | None = None,
            end_date: str | None = None, months: list[int] | None = None, wmo_id: str | None = None,
            pres_min: float | None = None, pres_max: float | None = None, depth_bin: int = 50,
            band_deg: float = 5, bins: int = 30) -> str:
    """Any statistic of temp/psal/doxy/chla/nitrate/bbp700 (or profile counts) by time, season, depth, float or
    lat/lon band, for named regions (incl. approximate oceans) or a box; compare up to 4 regions, floats or years.
    Returns the data with a status (ok, no_data with a diagnosis, low_data, too_many)."""
    return json.dumps(execute_tool("analyze", _given(locals())), default=str)


@mcp.tool()
def data_coverage() -> str:
    """What the database covers: floats, profiles, dates, area and values per measurement."""
    return json.dumps(execute_tool("data_coverage", {}), default=str)

if __name__ == "__main__":
    mcp.run(transport="stdio")
