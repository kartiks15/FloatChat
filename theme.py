"""Design tokens and shared Plotly styling (mirrored in assets/style.css)."""
import plotly.graph_objects as go

FONT = "Geist, system-ui, -apple-system, 'Segoe UI', sans-serif"
MONO = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SURFACE_2 = "#232327"
BORDER = "#2e2e33"
GRID = "#26262b"
INK = "#f4f4f5"
INK_2 = "#a1a1aa"
MUTED = "#8b8b94"
ACCENT = "#34d399"          # the one UI accent (emerald)
HIGHLIGHT = "#fbbf24"       # profiles highlighted by a region (amber, distinct from the selection)
PROFILE_DOT = "#a1a1aa"
# Data series, validated with the dataviz palette checks against the #18181b panel surface.
COLORS = {"temp": "#d95926", "psal": "#3987e5", "doxy": "#3987e5",
          "chla": "#199e70", "nitrate": "#c98500", "bbp700": "#d55181"}
PRESSURE_SCALE = [[0, "#b7d3f6"], [0.5, "#5598e7"], [1, "#1c5cab"]]  # one hue, shallow → deep
# Multi-series charts: dataviz reference dark categorical slots 1–4, validated on the #18181b surface.
SERIES_COLORS = ("#3987e5", "#d95926", "#199e70", "#c98500")

AXIS = dict(gridcolor=GRID, linecolor=BORDER, zeroline=False,
            tickfont=dict(family=MONO, color=MUTED, size=10), title=dict(font=dict(color=INK_2, size=12)))
BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=FONT, color=INK_2, size=12),
    margin=dict(l=56, r=16, t=36, b=44),
    hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=BORDER, font=dict(family=FONT, color=INK, size=12)),
)


def placeholder_figure(message, height=200):
    fig = go.Figure()
    fig.update_layout(**BASE_LAYOUT, height=height, xaxis=dict(visible=False), yaxis=dict(visible=False))
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(color=MUTED, size=13))
    return fig


def plural(count, noun):
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


def translucent(hex_color, alpha):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"
