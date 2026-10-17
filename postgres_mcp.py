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


if __name__ == "__main__":
    mcp.run(transport="stdio")
