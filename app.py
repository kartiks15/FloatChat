"""
FloatChat — Argo ocean data dashboard
=====================================
A map of float profiles, a profile inspector (temperature/salinity, BGC and a
T–S diagram) and an assistant that answers from fixed, read-only operations.

Layout:
  Top bar       — dataset totals and the period filter
  Left panel    — float catalogue, and the AI assistant with its settings
  Centre        — Plotly map of profile locations and the selected trajectory
  Right panel   — profile inspector charts

Run:
    python app.py
"""

import logging
import math
import os

from dash import Dash, dcc, html, Input, Output, State, ctx, no_update, ALL, Patch, clientside_callback
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from flask import request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from database import fetch_rows
from chat_service import answer_question, ai_enabled, user_keys_allowed, openrouter_models, MAX_QUERY_CHARS

load_dotenv()
log = logging.getLogger(__name__)

MAP_PROFILE_LIMIT = 5000   # database.fetch_rows refuses larger results
DATA_MODES = {"R": "Real-time", "A": "Real-time adjusted", "D": "Delayed mode"}


def db_query(sql, params=None):
    return fetch_rows(sql, params, max_rows=5000)


def get_profile_obs(profile_id):
    return db_query("""SELECT pres, temp, psal, doxy, chla, nitrate, bbp700,
        source_file, source_profile FROM observations WHERE profile_id=%s
        ORDER BY source_file, source_profile, pres LIMIT 5000""", [profile_id])


def get_profile_meta(profile_id):
    rows = db_query("""SELECT profile_id, wmo_id, cycle_number, direction, date_utc,
        latitude, longitude, data_mode FROM profiles WHERE profile_id=%s""", [profile_id])
    return rows[0] if rows else None


def get_trajectory(wmo_id):
    return db_query("""SELECT profile_id, wmo_id, cycle_number, date_utc, latitude, longitude
        FROM profiles WHERE wmo_id=%s AND latitude BETWEEN -90 AND 90
        AND longitude BETWEEN -180 AND 180 ORDER BY date_utc, profile_id LIMIT 2000""", [wmo_id])


def get_float_list():
    return db_query("""SELECT f.wmo_id, f.dac, COUNT(p.profile_id) n_profiles,
        MIN(p.date_utc) first_date, MAX(p.date_utc) last_date
        FROM floats f LEFT JOIN profiles p ON p.wmo_id=f.wmo_id
        GROUP BY f.wmo_id, f.dac ORDER BY n_profiles DESC, f.wmo_id LIMIT 500""")


def fmt_date(value):
    return str(value)[:10] if value else "—"


def fmt_position(lat, lon):
    if lat is None or lon is None:
        return "Position unknown"
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


# ── Design tokens (mirrored in assets/style.css) ───────────
FONT = "Geist, system-ui, -apple-system, 'Segoe UI', sans-serif"
MONO = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
SURFACE_2 = "#232327"
BORDER = "#2e2e33"
GRID = "#26262b"
INK = "#f4f4f5"
INK_2 = "#a1a1aa"
MUTED = "#8b8b94"
ACCENT = "#34d399"          # the one UI accent (emerald)
PROFILE_DOT = "#a1a1aa"
# Data series, validated with the dataviz palette checks against the #18181b panel surface.
COLORS = {"temp": "#d95926", "psal": "#3987e5", "doxy": "#3987e5",
          "chla": "#199e70", "nitrate": "#c98500", "bbp700": "#d55181"}
PRESSURE_SCALE = [[0, "#b7d3f6"], [0.5, "#5598e7"], [1, "#1c5cab"]]  # one hue, shallow → deep
BGC_VARIABLES = [("doxy", "Oxygen (µmol/kg)"), ("chla", "Chlorophyll-a (mg/m³)"),
                 ("nitrate", "Nitrate (µmol/kg)"), ("bbp700", "Backscatter 700 nm (m⁻¹)")]
# On wide screens the map sits under a 340 px left panel and a 420 px right panel;
# shifting the centre by half the difference frames data in the visible gap.
PANEL_OFFSET_PX = 40

AXIS = dict(gridcolor=GRID, linecolor=BORDER, zeroline=False,
            tickfont=dict(family=MONO, color=MUTED, size=10), title=dict(font=dict(color=INK_2, size=12)))
BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family=FONT, color=INK_2, size=12),
    margin=dict(l=56, r=16, t=36, b=44),
    hoverlabel=dict(bgcolor=SURFACE_2, bordercolor=BORDER, font=dict(family=FONT, color=INK, size=12)),
)


# ── Figures ────────────────────────────────────────────────
def placeholder_figure(message, height=200):
    fig = go.Figure()
    fig.update_layout(**BASE_LAYOUT, height=height, xaxis=dict(visible=False), yaxis=dict(visible=False))
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(color=MUTED, size=13))
    return fig


def style_subplots(fig, height):
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS, autorange="reversed")
    fig.update_yaxes(title_text="Pressure (dbar)", col=1)
    fig.update_annotations(font=dict(color=INK, size=12))
    fig.update_layout(**BASE_LAYOUT, height=height, showlegend=False)
    fig.update_layout(margin=dict(l=60, r=12, t=34, b=36))
    return fig


def map_view(lats, lons):
    """Centre and zoom that frame the given points; the Indian Ocean when empty."""
    if not lats:
        return dict(lat=5, lon=75), 2.2
    lat0, lat1, lon0, lon1 = min(lats), max(lats), min(lons), max(lons)
    # A 512 px world at zoom 0; leave margin for a ~620 px wide visible gap.
    span = max(lon1 - lon0, (lat1 - lat0) * 1.4, 2.0)
    zoom = max(0.8, min(7.5, math.log2(380 / span)))
    lon_shift = PANEL_OFFSET_PX * 360 / (512 * 2 ** zoom)
    return dict(lat=(lat0 + lat1) / 2, lon=(lon0 + lon1) / 2 + lon_shift), zoom


def trajectory_trace(traj):
    return go.Scattermap(
        lat=[t["latitude"] for t in traj], lon=[t["longitude"] for t in traj],
        mode="lines+markers", line=dict(width=2, color=ACCENT), marker=dict(size=6, color=ACCENT),
        customdata=[[t["profile_id"], t["wmo_id"]] for t in traj],
        text=[f"WMO {t['wmo_id']} · cycle {t['cycle_number']}<br>{fmt_date(t['date_utc'])}" for t in traj],
        hovertemplate="%{text}<extra></extra>", name="Selected float")


def selection_traces(meta):
    """A light halo under an accent dot marks the profile shown in the inspector."""
    lat = [meta["latitude"]] if meta and meta.get("latitude") is not None and meta.get("longitude") is not None else []
    lon = [meta["longitude"]] if lat else []
    return [go.Scattermap(lat=lat, lon=lon, mode="markers", hoverinfo="skip", name="halo",
                          marker=dict(size=18, color=INK, opacity=0.9)),
            go.Scattermap(lat=lat, lon=lon, mode="markers", hoverinfo="skip", name="Selected profile",
                          marker=dict(size=10, color=ACCENT))]


def build_map(profiles, selected_wmo=None, trajectory=None, selected=None):
    """Trace order is fixed (profiles, trajectory, halo, dot) so selection can be patched."""
    lats = [p["latitude"] for p in profiles]
    lons = [p["longitude"] for p in profiles]
    texts = []
    for p in profiles:
        t = p.get("temp_max")
        temp = f"Max temperature {t:.1f} °C" if t is not None else "Temperature unavailable"
        texts.append(f"WMO {p['wmo_id']} · {fmt_date(p['date_utc'])}<br>{temp}")
    fig = go.Figure(go.Scattermap(
        lat=lats, lon=lons, mode="markers", name="Profiles",
        marker=dict(size=7, color=PROFILE_DOT, opacity=0.55),
        text=texts, hovertemplate="%{text}<extra></extra>",
        customdata=[[p["profile_id"], p["wmo_id"]] for p in profiles]))
    traj = trajectory if trajectory is not None else (get_trajectory(selected_wmo) if selected_wmo else [])
    fig.add_trace(trajectory_trace(traj))
    fig.add_traces(selection_traces(selected))
    if traj:
        center, zoom = map_view([t["latitude"] for t in traj], [t["longitude"] for t in traj])
    else:
        center, zoom = map_view(lats, lons)
    fig.update_layout(**BASE_LAYOUT)
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), showlegend=False, uirevision="map",
                      map=dict(style="carto-darkmatter", center=center, zoom=zoom))
    return fig


def observation_groups(obs):
    groups = {}
    for row in obs:
        if row.get("pres") is not None:
            key = f"{row.get('source_file', 'legacy')} / grid {row.get('source_profile', 0)}"
            groups.setdefault(key, []).append(row)
    return groups


def profile_line(rows, key, source):
    # Separate source grids stay separate traces so they are never joined. Within one
    # grid, connect the levels where this variable was measured: merged (Sprof) grids
    # leave the other levels empty, which would otherwise break the line into dots.
    rows = [r for r in rows if r.get(key) is not None]
    return go.Scatter(x=[r[key] for r in rows], y=[r["pres"] for r in rows],
                      mode="lines" if len(rows) > 1 else "markers",
                      line=dict(color=COLORS[key], width=2), marker=dict(color=COLORS[key], size=6),
                      text=[source] * len(rows), showlegend=False,
                      hovertemplate="%{x:.3f} at %{y:.0f} dbar<br>%{text}<extra></extra>")


def build_profile_chart(profile_id, obs=None):
    obs = get_profile_obs(profile_id) if obs is None else obs
    groups = observation_groups(obs)
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.07,
                        subplot_titles=("Temperature (°C)", "Salinity (PSU)"))
    for source, rows in groups.items():
        for col, key in ((1, "temp"), (2, "psal")):
            if any(r.get(key) is not None for r in rows):
                fig.add_trace(profile_line(rows, key, source), row=1, col=col)
    if not fig.data:
        return placeholder_figure("No temperature or salinity in this profile", height=140)
    return style_subplots(fig, 320)


def build_bgc_chart(profile_id, obs=None):
    obs = get_profile_obs(profile_id) if obs is None else obs
    groups = observation_groups(obs)
    present = [(key, label) for key, label in BGC_VARIABLES
               if any(r.get(key) is not None for rows in groups.values() for r in rows)]
    if not present:
        return placeholder_figure("No biogeochemical measurements in this profile", height=120)
    # Different units need independent axes; two panels per row keeps them readable.
    n_rows = 1 if len(present) <= 2 else 2
    n_cols = min(len(present), 2)
    fig = make_subplots(rows=n_rows, cols=n_cols, shared_yaxes=True, horizontal_spacing=0.08,
                        vertical_spacing=0.16, subplot_titles=[label for _, label in present])
    for index, (key, _) in enumerate(present):
        for source, rows in groups.items():
            if any(r.get(key) is not None for r in rows):
                fig.add_trace(profile_line(rows, key, source), row=index // 2 + 1, col=index % 2 + 1)
    return style_subplots(fig, 300 if n_rows == 1 else 520)


def build_ts_diagram(wmo_id=None):
    """Temperature–salinity scatter for one float, or the latest levels overall."""
    sql = """
        SELECT o.temp, o.psal, o.pres, o.wmo_id
        FROM observations o
        WHERE o.temp IS NOT NULL AND o.psal IS NOT NULL AND o.pres IS NOT NULL
    """
    params = []
    if wmo_id:
        sql += " AND o.wmo_id = %s"
        params.append(wmo_id)
    sql += " ORDER BY o.profile_id DESC, o.pres LIMIT 5000"
    rows = db_query(sql, params or None)
    if not rows:
        return placeholder_figure("No paired temperature and salinity yet", height=140)
    fig = go.Figure(go.Scatter(
        x=[r["psal"] for r in rows], y=[r["temp"] for r in rows], mode="markers",
        marker=dict(size=4, opacity=0.75, color=[r["pres"] for r in rows], colorscale=PRESSURE_SCALE,
                    colorbar=dict(title=dict(text="dbar", font=dict(color=INK_2, size=11)),
                                  thickness=8, len=0.8, outlinewidth=0,
                                  tickfont=dict(color=MUTED, size=10))),
        hovertemplate="%{x:.2f} PSU · %{y:.2f} °C<br>%{marker.color:.0f} dbar<extra></extra>"))
    fig.update_xaxes(**AXIS, title_text="Salinity (PSU)")
    fig.update_yaxes(**AXIS, title_text="Temperature (°C)")
    fig.update_layout(**BASE_LAYOUT, height=300)
    fig.update_layout(margin=dict(l=60, r=12, t=12, b=44))
    return fig


# ── App ─────────────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=["https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap"],
    title="FloatChat · Argo ocean explorer",
    update_title=None,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"},
               {"name": "theme-color", "content": "#09090b"}],
)
server = app.server
server.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
if os.getenv("TRUST_PROXY", "false").lower() == "true":
    server.wsgi_app = ProxyFix(server.wsgi_app, x_for=1, x_proto=1)


@server.get("/healthz")
def healthz():
    return jsonify(status="ok")


@server.get("/readyz")
def readyz():
    try:
        db_query("SELECT profile_id FROM profile_summary LIMIT 1")
        db_query("SELECT source_file, source_profile FROM observations LIMIT 1")
        return jsonify(status="ready")
    except Exception:
        return jsonify(status="unavailable"), 503


@server.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ── Components ──────────────────────────────────────────────
SUGGESTIONS = ["Summarise the dataset", "List floats from INCOIS", "Profiles near 12°N 72°E"]


def icon(name, extra=""):
    """A stroke icon drawn by CSS masks (assets/style.css); decorative only."""
    return html.Span(className=f"icon icon-{name} {extra}".strip(), **{"aria-hidden": "true"})


def chat_bubble(text, role="assistant"):
    if role == "user":
        return html.Div(text, className="msg msg-user")
    return dcc.Markdown(text, className="msg msg-ai", link_target="_blank")


def stat_tile(label, value):
    return html.Div([html.Div(label, className="stat-label"), html.Div(value, className="stat-value")],
                    className="stat")


def kpi(value, label):
    return html.Div([html.Span(value, className="kpi-value"), html.Span(label, className="kpi-label")],
                    className="kpi")


def skeleton_kpis():
    return [html.Div(className="kpi skeleton", style={"width": f"{w}px"}) for w in (88, 112, 150)]


def skeleton_rows(count=6):
    return [html.Div([html.Div(className="skeleton line-lg"), html.Div(className="skeleton line-sm")],
                     className="skeleton-row") for _ in range(count)]


def status_chip(text, state):
    return [html.Span(className=f"status-dot is-{state}"), html.Span(text)]


def notice(icon_name, title, body, tone=""):
    """Composed empty and error states: left aligned, with a next step."""
    return html.Div([
        html.Div(icon(icon_name), className="notice-icon"),
        html.Div(title, className="notice-title"),
        html.P(body, className="notice-body"),
    ], className=f"notice {tone}".strip())


def float_item(f, selected, index=0):
    span = f"{f['first'][:4]}–{f['last'][:4]}" if f.get("first") and f.get("last") else "No profiles"
    return html.Button([
        html.Span(f["wmo_id"], className="float-wmo"),
        html.Span(span, className="float-span"),
        html.Span(f"{f['dac'] or '—'} · {f['n_profiles']:,} profiles", className="float-meta"),
    ], id={"type": "float-item", "index": f["wmo_id"]}, n_clicks=0, style={"--i": index},
        className="float-item is-selected" if selected else "float-item")


def welcome_message():
    if ai_enabled():
        access = "The assistant is on. "
    elif user_keys_allowed():
        access = "To chat, add your own OpenRouter API key under **AI settings** above. "
    else:
        access = "The assistant is off for this demo. "
    return ("Ask about the Argo floats in this database: where they went, what they measured, "
            "and individual profiles. Answers come only from the stored measurements.\n\n" + access +
            "The map and charts work without it.")


def ai_settings():
    # Always rendered so callbacks can reference every field; hidden when it has no use.
    own_keys = user_keys_allowed()
    useful = own_keys or os.getenv("AI_PROVIDER", "").lower() == "openrouter"
    models = openrouter_models()
    return html.Details([
        html.Summary([icon("chevron", "chevron"), html.Span("AI settings"),
                      html.Span([html.Span(className="status-dot"), html.Span(id="ai-status")],
                                id="ai-status-chip", className="ai-status")]),
        html.Div([
            html.Div([
                html.Label("OpenRouter API key", htmlFor="api-key-input", className="field-label"),
                html.Div([
                    html.Div([icon("key"), dcc.Input(id="api-key-input", type="password", placeholder="sk-or-v1-...",
                                                     autoComplete="off", className="text-input with-icon",
                                                     debounce=False)], className="input-wrap"),
                    html.Button("Save", id="save-key-btn", n_clicks=0, className="btn btn-primary"),
                    html.Button("Remove", id="clear-key-btn", n_clicks=0, className="btn btn-ghost"),
                ], className="key-row"),
                html.Div(id="key-feedback", className="field-help", role="status"),
            ], className="field", hidden=not own_keys),
            html.Div([
                html.Label("Model", htmlFor="model-select", className="field-label"),
                dcc.Dropdown(id="model-select", options=[{"label": label, "value": value} for value, label in models.items()],
                             value=next(iter(models)), clearable=False, searchable=False, className="dd"),
            ], className="field"),
            html.P(["Your key stays in this browser tab and is sent only with your questions, over HTTPS. "
                    "The server never stores it. Usage is billed to your OpenRouter account. ",
                    html.A("Get a key", href="https://openrouter.ai/keys", target="_blank", rel="noopener")],
                   className="field-help", hidden=not own_keys),
        ], className="settings-body"),
    ], className="ai-settings", id="ai-settings", hidden=not useful)


# ── Layout ──────────────────────────────────────────────────
# The map is a full-screen canvas; panels float over it on wide screens and
# stack below it on narrow ones (see assets/style.css).
app.layout = html.Div([
    dcc.Store(id="chat-history", data=[]),
    dcc.Store(id="selected-profile", data=None),
    dcc.Store(id="selected-wmo", data=None),
    dcc.Store(id="float-data", data=None),   # None while loading, {"error": ...} on failure
    dcc.Store(id="chat-selection", data=None),
    dcc.Store(id="api-key-store", storage_type="session"),
    dcc.Store(id="server-ai", data={"enabled": ai_enabled()}),
    dcc.Store(id="scroll-sink"),
    dcc.Store(id="view-sink"),

    html.Div(dcc.Graph(id="main-map", className="map-graph", figure=build_map([]),
                       config={"scrollZoom": True, "displayModeBar": False, "responsive": True}),
             className="map-stage"),

    html.Div([
        # Left column: brand, floats and assistant
        html.Div([
            html.Div([
                html.Div(icon("float"), className="brand-mark"),
                html.Div([html.Div("FloatChat", className="brand-name"),
                          html.Div("Argo ocean explorer", className="brand-sub")]),
            ], className="brand glass reveal", style={"--d": 0}),

            html.Aside([
                html.Div([
                    html.Button([icon("list"), "Floats"], id="tab-floats", n_clicks=0,
                                className="tab is-active", role="tab"),
                    html.Button([icon("sparkle"), "Ask AI"], id="tab-chat", n_clicks=0, className="tab", role="tab"),
                ], id="tabs", className="tabs", role="tablist"),

                html.Div([
                    html.Div([icon("search"), dcc.Input(id="float-search", type="search",
                                                        placeholder="Search WMO or data centre", debounce=False,
                                                        className="text-input with-icon")], className="input-wrap"),
                    html.Div(id="float-count", className="list-caption"),
                    html.Div(skeleton_rows(), id="float-list", className="float-list"),
                ], id="pane-floats", className="pane"),

                html.Div([
                    ai_settings(),
                    html.Div([chat_bubble(welcome_message())], id="chat-messages", className="messages",
                             **{"aria-live": "polite"}),
                    html.Div([html.Button(text, id={"type": "suggestion", "index": i}, n_clicks=0, className="chip")
                              for i, text in enumerate(SUGGESTIONS)], className="chips"),
                    dcc.Loading(html.Div(id="agent-loading"), delay_show=100, parent_className="chat-loading",
                                custom_spinner=html.Div([html.Span(), html.Span(), html.Span()], className="typing",
                                                        title="Thinking")),
                    html.Div([
                        dcc.Input(id="chat-input", type="text", placeholder="Ask about the ocean data",
                                  debounce=False, n_submit=0, maxLength=MAX_QUERY_CHARS, className="text-input"),
                        html.Button(icon("send"), id="send-btn", n_clicks=0, className="btn btn-primary btn-icon",
                                    title="Send", **{"aria-label": "Send"}),
                    ], className="composer"),
                ], id="pane-chat", className="pane is-hidden"),
            ], className="panel sidebar glass reveal", style={"--d": 80}),
        ], className="col-left"),

        # Centre column: toolbar and status float over the map
        html.Div([
            html.Div([
                html.Div(skeleton_kpis(), id="kpis", className="kpis"),
                html.Div(className="toolbar-rule"),
                html.Div([
                    html.Label("Period", htmlFor="date-filter"),
                    dcc.Dropdown(
                        id="date-filter",
                        options=[
                            {"label": "All time", "value": "all"},
                            {"label": "Last 30 days of data", "value": "30"},
                            {"label": "Last 90 days of data", "value": "90"},
                            {"label": "Last 180 days of data", "value": "180"},
                            {"label": "Last year of data", "value": "365"},
                        ],
                        value="all", clearable=False, searchable=False, className="dd",
                    ),
                ], className="period"),
            ], className="toolbar glass reveal", style={"--d": 160}),
            html.Div([
                html.Div(status_chip("Connecting", "idle"), id="db-status", className="status"),
                html.Div("Click a dot to inspect a profile", className="hint"),
            ], className="statusbar glass reveal", style={"--d": 240}),
        ], className="col-center"),

        # Right column: inspector
        html.Div([
            html.Aside([
                html.Div([
                    html.H2("Profile inspector", className="panel-title"),
                    html.Div(id="profile-meta", className="profile-meta"),
                ], className="panel-head"),
                dcc.Loading(
                    html.Div([
                        html.Div(notice("pin", "Pick a profile",
                                        "Click any dot on the map, or choose a float from the list. Its temperature, "
                                        "salinity and biogeochemistry profiles appear here."),
                                 id="inspector-empty"),
                        html.Div([
                            html.Div(id="profile-stats-row", className="stats"),
                            html.Section([html.H3("Temperature and salinity", className="chart-title"),
                                          dcc.Graph(id="ts-profile-chart", config={"displayModeBar": False})],
                                         className="chart-block"),
                            html.Section([html.H3("Biogeochemistry", className="chart-title"),
                                          dcc.Graph(id="bgc-chart", config={"displayModeBar": False})],
                                         className="chart-block"),
                        ], id="inspector-detail", className="inspector-detail", hidden=True),
                        html.Section([html.H3(id="ts-title", className="chart-title"),
                                      dcc.Graph(id="ts-diagram", config={"displayModeBar": False})],
                                     className="chart-block"),
                    ], className="inspector-body"),
                    # Keep the previous render visible and dimmed while refreshing; no layout jump.
                    overlay_style={"visibility": "visible", "opacity": 0.45}, delay_show=150,
                    parent_className="inspector-loading", custom_spinner=html.Div(className="progress-line"),
                ),
            ], className="panel inspector glass reveal", style={"--d": 120}),
        ], className="col-right"),
    ], className="shell"),
], className="app")


# ── Callbacks ───────────────────────────────────────────────
@app.callback(
    Output("float-data", "data"),
    Output("main-map", "figure"),
    Output("db-status", "children"),
    Output("kpis", "children"),
    Input("date-filter", "value"),
    State("selected-wmo", "data"),
    State("selected-profile", "data"),
)
def load_data(date_filter, selected_wmo=None, selected_profile=None):
    # Relative to the dataset's latest measurement so historical samples remain useful.
    if date_filter not in ("all", "30", "90", "180", "365"):
        date_filter = "all"
    date_clause = ""
    params = []
    if date_filter != "all":
        date_clause = "AND date_utc >= (SELECT MAX(date_utc) FROM profiles) - (%s * INTERVAL '1 day')"
        params.append(int(date_filter))
    try:
        profiles = db_query(f"""SELECT profile_id, wmo_id, latitude, longitude, date_utc, temp_max
            FROM profile_summary WHERE latitude BETWEEN -90 AND 90
            AND longitude BETWEEN -180 AND 180 {date_clause}
            ORDER BY date_utc DESC NULLS LAST, profile_id DESC LIMIT {MAP_PROFILE_LIMIT}""", params)
        floats = get_float_list()
        trajectory = get_trajectory(selected_wmo) if selected_wmo else []
        selected = get_profile_meta(selected_profile) if selected_profile else None
    except Exception as exc:
        log.warning("Data unavailable (%s)", type(exc).__name__)
        return ({"error": "database"}, build_map([]), status_chip("Database unavailable", "error"),
                [kpi("—", "floats"), kpi("—", "profiles")])

    float_data = [{"wmo_id": f["wmo_id"], "dac": f["dac"], "n_profiles": f["n_profiles"],
                   "first": fmt_date(f["first_date"]) if f["first_date"] else None,
                   "last": fmt_date(f["last_date"]) if f["last_date"] else None} for f in floats]
    if profiles:
        status = f"{len(profiles):,} profiles on the map"
        if len(profiles) == MAP_PROFILE_LIMIT:
            status += " (latest shown)"
    else:
        status = "No profiles in this period"
    latest = max((f["last"] for f in float_data if f["last"]), default=None)
    kpis = [kpi(f"{len(float_data):,}", "floats"),
            kpi(f"{sum(f['n_profiles'] for f in float_data):,}", "profiles"),
            kpi(latest or "—", "latest")]
    return float_data, build_map(profiles, selected_wmo, trajectory, selected), status_chip(status, "live"), kpis


@app.callback(
    Output("float-list", "children"),
    Output("float-count", "children"),
    Input("float-data", "data"),
    Input("float-search", "value"),
    State("selected-wmo", "data"),
)
def render_float_list(floats, search, selected_wmo):
    if floats is None:
        return skeleton_rows(), ""
    if isinstance(floats, dict):
        return notice("alert", "Database unavailable",
                      "The float list and map load once the database is reachable. Try again in a minute.",
                      "is-error"), ""
    if not floats:
        return notice("list", "No floats yet",
                      "Import Argo NetCDF files with the ETL (docker compose run --rm etl) and they appear here."), ""
    term = (search or "").strip().lower()
    shown = [f for f in floats if term in f["wmo_id"] or term in (f["dac"] or "").lower()] if term else floats
    caption = f"{len(shown)} of {len(floats)} floats" if term else f"{len(floats)} floats"
    if not shown:
        return notice("search", "No matching floats", "Try part of a WMO number, or a data centre code such as IN or AO."), caption
    return [float_item(f, f["wmo_id"] == selected_wmo, i) for i, f in enumerate(shown)], caption


@app.callback(
    Output("selected-profile", "data"),
    Output("selected-wmo", "data"),
    Input("main-map", "clickData"),
    Input({"type": "float-item", "index": ALL}, "n_clicks"),
    Input("chat-selection", "data"),
    prevent_initial_call=True,
)
def select_profile(click_data, float_clicks, chat_selection):
    trigger = ctx.triggered_id
    if trigger == "chat-selection" and chat_selection:
        return chat_selection["profile_id"], chat_selection["wmo_id"]
    if isinstance(trigger, dict) and trigger.get("type") == "float-item" and any(float_clicks or []):
        wmo = trigger["index"]
        try:
            rows = db_query("SELECT profile_id FROM profiles WHERE wmo_id=%s ORDER BY date_utc DESC NULLS LAST, profile_id DESC LIMIT 1", [wmo])
            return rows[0]["profile_id"] if rows else None, wmo
        except Exception:
            return no_update, no_update
    if trigger == "main-map" and click_data and click_data.get("points"):
        data = click_data["points"][0].get("customdata")
        if isinstance(data, (list, tuple)) and len(data) == 2:
            return data[0], data[1]
    return no_update, no_update


@app.callback(
    Output("main-map", "figure", allow_duplicate=True),
    Input("selected-wmo", "data"),
    Input("selected-profile", "data"),
    prevent_initial_call=True,
)
def update_map_selection(wmo_id, profile_id):
    """Patch only the selection traces instead of resending every profile."""
    try:
        traj = get_trajectory(wmo_id) if wmo_id else []
        meta = get_profile_meta(profile_id) if profile_id else None
    except Exception:
        return no_update
    patch = Patch()
    patch["data"][1] = trajectory_trace(traj).to_plotly_json()
    halo, dot = selection_traces(meta)
    patch["data"][2] = halo.to_plotly_json()
    patch["data"][3] = dot.to_plotly_json()
    if "selected-wmo.data" in ctx.triggered_prop_ids and traj:
        center, zoom = map_view([t["latitude"] for t in traj], [t["longitude"] for t in traj])
        patch["layout"]["map"]["center"] = center
        patch["layout"]["map"]["zoom"] = zoom
    return patch


@app.callback(
    Output("ts-profile-chart", "figure"),
    Output("bgc-chart",        "figure"),
    Output("ts-diagram",       "figure"),
    Output("profile-meta",     "children"),
    Output("profile-stats-row","children"),
    Output("ts-title",         "children"),
    Output("inspector-empty",  "hidden"),
    Output("inspector-detail", "hidden"),
    Input("selected-profile", "data"),
    Input("selected-wmo",     "data"),
)
def update_profile_charts(profile_id, wmo_id):
    ts_title = f"T–S diagram · WMO {wmo_id}" if wmo_id else "T–S diagram · latest 5,000 levels"
    try:
        obs = get_profile_obs(profile_id) if profile_id else []
        meta = get_profile_meta(profile_id) if profile_id else None
        ts_diag = build_ts_diagram(wmo_id)
    except Exception:
        blank = placeholder_figure("Database unavailable", height=140)
        error = html.Span([icon("alert"), "Database unavailable"], className="meta-error")
        return blank, blank, blank, error, [], ts_title, False, True
    if not profile_id:
        blank = placeholder_figure("", height=60)
        return blank, blank, ts_diag, html.Span("No profile selected", className="muted"), [], ts_title, False, True

    details = [html.Span(f"WMO {wmo_id}", className="badge")]
    if meta:
        details += [html.Span(f"Cycle {meta['cycle_number']}{'D' if meta.get('direction') == 'D' else ''}"),
                    html.Span(fmt_date(meta["date_utc"]), className="mono"),
                    html.Span(fmt_position(meta["latitude"], meta["longitude"]), className="mono")]
        if meta.get("data_mode") in DATA_MODES:
            details.append(html.Span(DATA_MODES[meta["data_mode"]]))
    stats = []
    temps = [o["temp"] for o in obs if o.get("temp") is not None]
    psals = [o["psal"] for o in obs if o.get("psal") is not None]
    pres = [o["pres"] for o in obs if o.get("pres") is not None]
    if temps:
        stats.append(stat_tile("Max temperature", f"{max(temps):.1f} °C"))
    if psals:
        stats.append(stat_tile("Mean salinity", f"{sum(psals) / len(psals):.2f} PSU"))
    if pres:
        stats.append(stat_tile("Deepest level", f"{max(pres):,.0f} dbar"))
    stats.append(stat_tile("Levels", f"{len(obs):,}"))
    return (build_profile_chart(profile_id, obs), build_bgc_chart(profile_id, obs), ts_diag,
            details, stats, ts_title, True, False)


@app.callback(
    Output("chat-messages", "children"),
    Output("chat-history",  "data"),
    Output("chat-input",    "value"),
    Output("agent-loading", "children"),
    Output("chat-selection", "data"),
    Input("send-btn",   "n_clicks"),
    Input("chat-input", "n_submit"),
    State("chat-input",    "value"),
    State("chat-messages", "children"),
    State("chat-history",  "data"),
    State("api-key-store", "data"),
    State("model-select", "value"),
    prevent_initial_call=True,
    running=[(Output("send-btn", "disabled"), True, False), (Output("chat-input", "disabled"), True, False)],
)
def handle_chat(n_clicks, n_submit, query, messages, history, api_key=None, model=None):
    if not query or not query.strip():
        return no_update, no_update, no_update, no_update, no_update
    messages = (messages or [])[-12:] + [chat_bubble(query, role="user")]
    history = (history or [])[-6:]
    # The visitor's key (if any) is used for this request only; it is never logged or stored.
    response, selection = answer_question(query, history, request.remote_addr or "unknown",
                                          api_key=api_key, model=model)
    messages = messages + [chat_bubble(response)]
    history = history + [{"role": "human", "content": query}, {"role": "assistant", "content": response}]
    return messages[-12:], history[-6:], "", "", selection or no_update


@app.callback(
    Output("chat-input", "value", allow_duplicate=True),
    Input({"type": "suggestion", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def use_suggestion(clicks):
    trigger = ctx.triggered_id
    if isinstance(trigger, dict) and any(clicks or []):
        return SUGGESTIONS[trigger["index"]]
    return no_update


# Tabs switch in the browser; no server round trip. The tablist class moves the sliding indicator.
clientside_callback(
    """
    function(floatsClicks, chatClicks) {
        const chat = (dash_clientside.callback_context.triggered[0] || {}).prop_id === "tab-chat.n_clicks";
        return [chat ? "tabs is-chat" : "tabs",
                chat ? "tab" : "tab is-active", chat ? "tab is-active" : "tab",
                chat ? "pane is-hidden" : "pane", chat ? "pane" : "pane is-hidden"];
    }
    """,
    Output("tabs", "className"),
    Output("tab-floats", "className"), Output("tab-chat", "className"),
    Output("pane-floats", "className"), Output("pane-chat", "className"),
    Input("tab-floats", "n_clicks"), Input("tab-chat", "n_clicks"),
    prevent_initial_call=True,
)

# map_view() frames data for the ~620 px gap between the desktop panels. Correct the zoom
# (and drop the panel offset when panels stack) for the real map width, once per new view
# from the server, so later manual panning is left alone.
clientside_callback(
    """
    function(fig) {
        const m = fig && fig.layout && fig.layout.map;
        if (!m || !m.center) { return dash_clientside.no_update; }
        const key = JSON.stringify([m.center, m.zoom]);
        if (window.__floatchatView === key) { return dash_clientside.no_update; }
        window.__floatchatView = key;
        setTimeout(() => {
            const gd = document.querySelector("#main-map .js-plotly-plot");
            if (!gd || !window.Plotly) { return; }
            const wide = window.matchMedia("(min-width: 1280px)").matches;
            const visible = wide ? gd.clientWidth - (340 + 420 + 16 * 4) : gd.clientWidth;
            const zoom = Math.max(0.5, m.zoom + Math.log2(Math.max(visible, 200) / 620));
            const lon = wide ? m.center.lon : m.center.lon - 40 * 360 / (512 * Math.pow(2, m.zoom));
            window.Plotly.relayout(gd, {"map.zoom": zoom, "map.center": {lat: m.center.lat, lon: lon}});
        }, 250);
        return dash_clientside.no_update;
    }
    """,
    Output("view-sink", "data"),
    Input("main-map", "figure"),
)

# Keep the newest chat message in view.
clientside_callback(
    """
    function(children) {
        setTimeout(() => {
            const el = document.getElementById("chat-messages");
            if (el) { el.scrollTo({top: el.scrollHeight, behavior: "smooth"}); }
        }, 60);
        return dash_clientside.no_update;
    }
    """,
    Output("scroll-sink", "data"),
    Input("chat-messages", "children"),
    prevent_initial_call=True,
)

# Highlight the selected float without re-rendering (and re-animating) the list.
clientside_callback(
    """
    function(wmo, ids) {
        return (ids || []).map(id => id.index === wmo ? "float-item is-selected" : "float-item");
    }
    """,
    Output({"type": "float-item", "index": ALL}, "className"),
    Input("selected-wmo", "data"),
    State({"type": "float-item", "index": ALL}, "id"),
    prevent_initial_call=True,
)

# The key is saved to sessionStorage in the browser, never through the server.
clientside_callback(
    """
    function(saveClicks, clearClicks, value) {
        const trigger = (dash_clientside.callback_context.triggered[0] || {}).prop_id || "";
        const skip = dash_clientside.no_update;
        if (trigger.startsWith("clear-key-btn")) { return [null, "", "Key removed from this tab."]; }
        if (!trigger.startsWith("save-key-btn")) { return [skip, skip, skip]; }
        const key = (value || "").trim();
        if (!/^sk-or-[A-Za-z0-9_-]{16,200}$/.test(key)) {
            return [skip, skip, "That doesn't look like an OpenRouter key. Keys start with sk-or-."];
        }
        return [key, "", "Key saved for this tab."];
    }
    """,
    Output("api-key-store", "data"), Output("api-key-input", "value"), Output("key-feedback", "children"),
    Input("save-key-btn", "n_clicks"), Input("clear-key-btn", "n_clicks"),
    State("api-key-input", "value"),
    prevent_initial_call=True,
)
clientside_callback(
    """
    function(key, server) {
        if (key) { return ["Your key ending " + key.slice(-4), "ai-status is-on"]; }
        if (server && server.enabled) { return ["Server model on", "ai-status is-on"]; }
        return ["Off", "ai-status"];
    }
    """,
    Output("ai-status", "children"), Output("ai-status-chip", "className"),
    Input("api-key-store", "data"), State("server-ai", "data"),
)


if __name__ == "__main__":
    app.run(debug=False, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8050")))
