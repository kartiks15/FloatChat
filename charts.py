"""Plotly figures and captions for analysis results (see analysis.run_analysis)."""
import plotly.graph_objects as go

from analysis import MEASURES, STAT_LABELS
from regions import MONTH_NAMES
from theme import (ACCENT, AXIS, BASE_LAYOUT, BORDER, COLORS, INK_2, PRESSURE_SCALE, SERIES_COLORS, SURFACE,
                   SURFACE_2, placeholder_figure, plural)

MESSAGE_STATUSES = ("no_data", "too_many", "unsupported")
TOO_MANY_MESSAGE = "Too many points to chart. Choose a coarser grouping or a shorter period."
CAPTION_STAT = {"mean": "Average of each profile's mean", "median": "Median of profile means",
                "std": "Standard deviation of profile means", "p10": "10th percentile of profile means",
                "p90": "90th percentile of profile means", "min": "Lowest observed value",
                "max": "Highest observed value", "count": "Number of profiles"}
X_TITLES = {"day": "Day", "week": "Week starting", "month": "Month", "year": "Year", "float": "Float (WMO)",
            "lat_band": "Latitude band start (°)", "lon_band": "Longitude band start (°)"}
FADED = 0.45   # low-confidence points (fewer than 3 profiles)


def _value_title(result, with_layer=True):
    if result["measure"] == "profiles":
        return "Number of profiles"
    title = f"{STAT_LABELS[result['stat']]} {result['measure_label'].lower()} ({result['units']})"
    lo, hi = result["pressure_range"]
    return title + (f", {lo:g}–{hi:g} dbar" if with_layer else "")


def _hover(point):
    text = f"{plural(point['n_profiles'], 'profile')} · {plural(point['n_floats'], 'float')}"
    return text + (" · low confidence" if point["low"] else "")


def _grouped(result):
    """Line, bar and depth-profile charts: one trace per series."""
    fig = go.Figure()
    many = len(result["series"]) > 1
    chart, group = result["chart"], result["group_by"]
    half_bin = result.get("depth_bin", 50) / 2
    units = result["units"]
    for index, series in enumerate(result["series"]):
        points = series["points"]
        color = SERIES_COLORS[index] if many else COLORS.get(result["measure"], ACCENT)
        x = [MONTH_NAMES[p["x"] - 1] if group == "month_of_year" else p["x"] for p in points]
        y = [p["y"] for p in points]
        opacity = [FADED if p["low"] else 1 for p in points]
        hover = [_hover(p) for p in points]
        if chart == "bar":
            fig.add_trace(go.Bar(x=x, y=y, name=series["name"], customdata=hover,
                                 marker=dict(color=color, opacity=opacity, line=dict(width=0)),
                                 hovertemplate=f"%{{x}}: %{{y:.3g}} {units}<br>%{{customdata}}<extra>%{{fullData.name}}</extra>"))
            continue
        marker = dict(color=color, size=8, opacity=opacity, line=dict(color=color, width=2),
                      symbol=["circle-open" if p["low"] else "circle" for p in points])
        mode = "lines+markers" if len(points) > 1 else "markers"
        if chart == "profile":
            depth = [p["x"] + half_bin for p in points]
            fig.add_trace(go.Scatter(x=y, y=depth, name=series["name"], mode=mode, line=dict(color=color, width=2),
                                     marker=marker, customdata=hover,
                                     hovertemplate=f"%{{x:.3g}} {units} at %{{y:.0f}} dbar<br>%{{customdata}}"
                                                   "<extra>%{fullData.name}</extra>"))
        else:
            fig.add_trace(go.Scatter(x=x, y=y, name=series["name"], mode=mode, line=dict(color=color, width=2),
                                     marker=marker, customdata=hover,
                                     hovertemplate=f"%{{y:.3g}} {units}<br>%{{customdata}}<extra>%{{fullData.name}}</extra>"))
            if many and points:   # direct label at each line's end
                fig.add_annotation(x=x[-1], y=y[-1], text=series["name"], showarrow=False, xanchor="left",
                                   xshift=8, font=dict(color=INK_2, size=11))
    if chart == "profile":
        fig.update_xaxes(**AXIS, title_text=_value_title(result, with_layer=False))
        fig.update_yaxes(**AXIS, title_text="Pressure (dbar)", autorange="reversed")
    else:
        axis_type = ("category" if group in ("month_of_year", "float", "none")
                     else "date" if group in ("day", "week", "month") else "linear")
        fig.update_xaxes(**AXIS, type=axis_type, title_text=X_TITLES.get(group, ""))
        fig.update_yaxes(**AXIS, title_text=_value_title(result))
    fig.update_layout(barmode="group", hovermode="x unified" if chart == "line" else "closest")
    return fig


def _histogram(result):
    points = result["points"]
    color = COLORS.get(result["measure"], ACCENT)
    fig = go.Figure(go.Bar(
        x=[p["x"] for p in points], y=[p["y"] for p in points], width=[(p["x1"] - p["x0"]) * 0.94 for p in points],
        customdata=[[p["x0"], p["x1"]] for p in points], marker=dict(color=color, line=dict(width=0)),
        hovertemplate=f"%{{customdata[0]:.3g}}–%{{customdata[1]:.3g}} {result['units']}: %{{y}} profiles<extra></extra>"))
    lo, hi = result["pressure_range"]
    fig.update_xaxes(**AXIS, title_text=f"Profile mean {result['measure_label'].lower()} ({result['units']}), "
                                        f"{lo:g}–{hi:g} dbar")
    fig.update_yaxes(**AXIS, title_text="Profiles")
    return fig


def _scatter(result):
    points = result["points"]
    _, x_label, x_units = MEASURES[result["x_measure"]]
    fig = go.Figure(go.Scatter(
        x=[p["x"] for p in points], y=[p["y"] for p in points], mode="markers",
        marker=dict(size=5, opacity=0.8, color=[p["pres"] for p in points], colorscale=PRESSURE_SCALE,
                    colorbar=dict(title=dict(text="dbar", font=dict(color=INK_2, size=11)), thickness=8, len=0.8,
                                  outlinewidth=0)),
        hovertemplate=f"%{{x:.3g}} {x_units} · %{{y:.3g}} {result['units']}<br>%{{marker.color:.0f}} dbar<extra></extra>"))
    fig.update_xaxes(**AXIS, title_text=f"{x_label} ({x_units})")
    fig.update_yaxes(**AXIS, title_text=f"{result['measure_label']} ({result['units']})")
    return fig


def _map(result):
    """Profiles coloured by value. A geo chart draws its own coastlines: no tile style to load, so
    updates can't be lost the way they are when a MapLibre map is still fetching its style."""
    points = result["points"]
    fig = go.Figure(go.Scattergeo(
        lat=[p["lat"] for p in points], lon=[p["lon"] for p in points], mode="markers",
        customdata=[[p["profile_id"], p["wmo_id"]] for p in points],
        marker=dict(size=6, color=[p["value"] for p in points], colorscale=PRESSURE_SCALE, line=dict(width=0),
                    colorbar=dict(title=dict(text=result["units"], font=dict(color=INK_2, size=11)), thickness=8,
                                  len=0.8, outlinewidth=0)),
        hovertemplate=f"WMO %{{customdata[1]}}<br>%{{marker.color:.3g}} {result['units']}<extra></extra>"))
    fig.update_geos(projection_type="natural earth", fitbounds="locations", bgcolor="rgba(0,0,0,0)",
                    showland=True, landcolor=SURFACE_2, showocean=True, oceancolor=SURFACE,
                    showcoastlines=True, coastlinecolor=BORDER, showcountries=False, showframe=False,
                    showlakes=False)
    return fig


def build_chart(result):
    if result.get("status") in MESSAGE_STATUSES:
        return placeholder_figure("", height=40)
    chart = result["chart"]
    builder = {"histogram": _histogram, "scatter": _scatter, "map": _map}.get(chart, _grouped)
    fig = builder(result)
    many = len(result.get("series") or []) > 1
    fig.update_layout(**BASE_LAYOUT)
    fig.update_layout(height=300 if chart == "map" else 260, showlegend=many,
                      legend=dict(orientation="h", x=0, y=1.14, font=dict(color=INK_2, size=11)),
                      margin=dict(l=8, r=8, t=8, b=8) if chart == "map" else dict(l=60, r=16, t=28 if many else 8, b=36))
    return fig


def describe_result(result):
    """Title, caption and (instead of a chart) a message for the Analysis panel."""
    status = result.get("status")
    if status == "unsupported":
        return {"title": "No analysis", "caption": "", "message": f"{result['reason']} {result['supported']}"}
    if status == "no_data":
        return {"title": result["title"], "caption": "", "message": result["diagnosis"]}
    if status == "too_many":
        return {"title": result["title"], "caption": "", "message": TOO_MANY_MESSAGE}
    parts = []
    lo, hi = result["pressure_range"]
    if result["chart"] == "scatter":
        parts.append(f"Levels between {lo:g} and {hi:g} dbar")
    elif result["measure"] != "profiles":
        stat = "mean" if result["chart"] in ("histogram", "map") else result["stat"]
        parts.append(f"{CAPTION_STAT[stat]}, {lo:g}–{hi:g} dbar")
    series = result.get("series") or []
    if len(series) == 1:
        only = series[0]
        parts.append(f"{plural(only['n_profiles'], 'profile')} from {plural(only['n_floats'], 'float')}")
        if only.get("first_date"):
            parts.append(only["first_date"] if only["first_date"] == only["last_date"]
                         else f"{only['first_date']} to {only['last_date']}")
    elif series:
        parts.append(f"{plural(result['n_profiles'], 'profile')} across {len(series)} series")
    else:
        parts.append(f"{plural(result['n_profiles'], 'profile')} from {plural(result['n_floats'], 'float')}")
    return {"title": result["title"], "caption": " · ".join(parts + result.get("notes", [])), "message": None}
