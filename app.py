"""
FloatChat — AI-Powered ARGO Ocean Data Dashboard
=================================================
Full frontend: chat interface + geospatial map + depth profiles + BGC charts.

Layout:
  Left sidebar  — chat panel (NL query → agent → response)
  Center        — Plotly map (float trajectories / scatter)
  Right panel   — profile charts (T/S depth, BGC parameters)

Run:
    python app.py
"""

import logging
import os

from dash import Dash, dcc, html, Input, Output, State, ctx, no_update, ALL
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from flask import request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from database import fetch_rows
from chat_service import answer_question, ai_enabled, MAX_QUERY_CHARS

load_dotenv()
log = logging.getLogger(__name__)


def db_query(sql, params=None):
    return fetch_rows(sql, params, max_rows=5000)


def get_profile_obs(profile_id):
    return db_query("""SELECT pres, temp, psal, doxy, chla, nitrate, bbp700,
        source_file, source_profile FROM observations WHERE profile_id=%s
        ORDER BY source_file, source_profile, pres LIMIT 5000""", [profile_id])


def get_trajectory(wmo_id):
    return db_query("""SELECT profile_id, wmo_id, cycle_number, date_utc, latitude, longitude
        FROM profiles WHERE wmo_id=%s AND latitude BETWEEN -90 AND 90
        AND longitude BETWEEN -180 AND 180 ORDER BY date_utc, profile_id LIMIT 2000""", [wmo_id])


def get_float_list():
    return db_query("""SELECT f.wmo_id, f.dac, COUNT(p.profile_id) n_profiles,
        MIN(p.date_utc) first_date, MAX(p.date_utc) last_date
        FROM floats f LEFT JOIN profiles p ON p.wmo_id=f.wmo_id
        GROUP BY f.wmo_id, f.dac ORDER BY n_profiles DESC, f.wmo_id LIMIT 50""")


# ── Color palette ──────────────────────────────────────────
OCEAN_DARK   = "#040d1a"
OCEAN_MID    = "#071a2e"
OCEAN_CARD   = "#0a2240"
OCEAN_BORDER = "#0e3060"
ACCENT_CYAN  = "#00d4ff"
ACCENT_TEAL  = "#00b4a0"
ACCENT_WARM  = "#f0a500"
TEXT_PRIMARY = "#e8f4fd"
TEXT_MUTED   = "#7aacc8"
GRADIENT_MAP = "Viridis"

# ── Plotly theme ───────────────────────────────────────────
PLOTLY_THEME = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(4,13,26,0.6)",
    font=dict(family="'Space Mono', monospace", color=TEXT_PRIMARY, size=11),
    margin=dict(l=40, r=20, t=30, b=40),
    xaxis=dict(gridcolor="#0e3060", zerolinecolor="#0e3060", color=TEXT_MUTED),
    yaxis=dict(gridcolor="#0e3060", zerolinecolor="#0e3060", color=TEXT_MUTED),
)

# ── Build figures ──────────────────────────────────────────
def build_map(profiles, selected_wmo=None):
    fig = go.Figure()

    if not profiles:
        fig.update_layout(
            **PLOTLY_THEME,
            map=dict(style="carto-darkmatter", center=dict(lat=15, lon=72), zoom=3),
            height=480,
        )
        return fig

    lats  = [p["latitude"]  for p in profiles]
    lons  = [p["longitude"] for p in profiles]
    wmos  = [p["wmo_id"]    for p in profiles]
    dates = [str(p["date_utc"])[:10] if p["date_utc"] else "" for p in profiles]
    temps = [p.get("temp_max") for p in profiles]
    texts = [f"WMO: {w}<br>Date: {d}<br>T_max: {t:.1f}°C" if t is not None else f"WMO: {w}<br>Date: {d}<br>Temperature unavailable"
             for w, d, t in zip(wmos, dates, temps)]

    fig.add_trace(go.Scattermap(
        lat=lats, lon=lons, mode="markers",
        marker=dict(size=7, color=[t if t is not None else "#7aacc8" for t in temps], colorscale="Thermal",
                    colorbar=dict(title="T max °C", thickness=10, len=0.5,
                                  tickfont=dict(color=TEXT_MUTED, size=9)),
                    opacity=0.85),
        text=texts, hoverinfo="text",
        customdata=[[p["profile_id"], p["wmo_id"]] for p in profiles],
        name="Profiles",
    ))

    # Highlight selected float trajectory
    if selected_wmo:
        traj = get_trajectory(selected_wmo)
        if traj:
            fig.add_trace(go.Scattermap(
                lat=[t["latitude"] for t in traj],
                lon=[t["longitude"] for t in traj],
                mode="lines+markers",
                line=dict(width=2, color=ACCENT_CYAN),
                marker=dict(size=5, color=ACCENT_CYAN),
                name=f"WMO {selected_wmo}",
                customdata=[[t["profile_id"], t["wmo_id"]] for t in traj],
            ))

    fig.update_layout(
        **PLOTLY_THEME,
        map=dict(style="carto-darkmatter",
                    center=dict(lat=sum(lats)/len(lats), lon=sum(lons)/len(lons)),
                    zoom=3),
        height=480,
        showlegend=bool(selected_wmo),
        legend=dict(bgcolor="rgba(10,34,64,0.8)", bordercolor=OCEAN_BORDER,
                    font=dict(color=TEXT_PRIMARY, size=10)),
    )
    return fig


def build_profile_chart(profile_id, obs=None):
    obs = get_profile_obs(profile_id) if obs is None else obs
    if not obs:
        return go.Figure().update_layout(**PLOTLY_THEME, height=320,
            title=dict(text="No observations found", font=dict(color=TEXT_MUTED)))

    fig = go.Figure()
    for group_index, (source, rows) in enumerate(observation_groups(obs).items()):
        for key, label, color, axis in [("temp", "Temperature (°C)", ACCENT_WARM, "x"),
                                        ("psal", "Salinity (PSU)", ACCENT_CYAN, "x2")]:
            fig.add_trace(go.Scatter(x=[r.get(key) for r in rows], y=[r["pres"] for r in rows],
                mode="lines+markers", name=label, legendgroup=key, showlegend=group_index == 0,
                line=dict(color=color, width=2), marker=dict(size=3), xaxis=axis,
                text=[source] * len(rows), hovertemplate="%{x}<br>%{y} dbar<br>%{text}<extra></extra>"))

    theme_base = {k: v for k, v in PLOTLY_THEME.items() if k not in ['xaxis', 'yaxis']}
    xaxis_custom = {**PLOTLY_THEME["xaxis"], "title": "Temperature °C", "color": ACCENT_WARM}
    yaxis_custom = {**PLOTLY_THEME["yaxis"], "autorange": "reversed", "title": "Pressure (dbar)"}
    fig.update_layout(
        **theme_base,
        height=320,
        title=dict(text=f"Profile #{profile_id} — T/S vs Pressure",
                   font=dict(color=TEXT_PRIMARY, size=12)),
        yaxis=yaxis_custom,
        xaxis=xaxis_custom,
        xaxis2=dict(title="Salinity PSU", color=ACCENT_CYAN,
                    overlaying="x", side="top", gridcolor="rgba(0,0,0,0)"),
        legend=dict(bgcolor="rgba(10,34,64,0.7)", bordercolor=OCEAN_BORDER,
                    font=dict(color=TEXT_PRIMARY, size=10), x=0.65, y=0.98),
    )
    return fig


def observation_groups(obs):
    groups = {}
    for row in obs:
        if row.get("pres") is not None:
            key = f"{row.get('source_file', 'legacy')} / grid {row.get('source_profile', 0)}"
            groups.setdefault(key, []).append(row)
    return groups


def build_bgc_chart(profile_id, obs=None):
    obs = get_profile_obs(profile_id) if obs is None else obs
    variables = [("doxy", ACCENT_CYAN, "O₂ μmol/kg"),
                 ("chla", "#00ff9d", "Chl-a mg/m³"),
                 ("nitrate", ACCENT_WARM, "NO₃ μmol/kg")]
    # Different units need independent axes; separate grids must not be connected.
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True, horizontal_spacing=0.09)
    has_data = False
    for column, (key, color, label) in enumerate(variables, 1):
        for source, rows in observation_groups(obs).items():
            if any(row.get(key) is not None for row in rows):
                has_data = True
                fig.add_trace(go.Scatter(x=[r.get(key) for r in rows], y=[r["pres"] for r in rows],
                    mode="lines+markers", line=dict(color=color, width=2), marker=dict(size=3),
                    showlegend=False, text=[source] * len(rows),
                    hovertemplate="%{x}<br>%{y} dbar<br>%{text}<extra></extra>"), row=1, col=column)
        fig.update_xaxes(title_text=label, gridcolor=OCEAN_BORDER, row=1, col=column)
    fig.update_yaxes(autorange="reversed", gridcolor=OCEAN_BORDER)
    fig.update_yaxes(title_text="Pressure (dbar)", row=1, col=1)
    if not has_data:
        fig.add_annotation(text="No BGC measurements for this profile", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False)
    fig.update_layout(**{k:v for k,v in PLOTLY_THEME.items() if k not in ("xaxis", "yaxis")},
                      height=300, title=dict(text=f"Profile #{profile_id} — BGC", font=dict(size=12)))
    return fig


def build_ts_diagram(wmo_id=None):
    """Temperature–Salinity scatter diagram."""
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

    fig = go.Figure()
    if rows:
        fig.add_trace(go.Scatter(
            x=[r["psal"] for r in rows],
            y=[r["temp"] for r in rows],
            mode="markers",
            marker=dict(size=3, color=[r["pres"] for r in rows],
                        colorscale="Viridis", opacity=0.6,
                        colorbar=dict(title="Pressure (dbar)", thickness=10, len=0.6,
                                      tickfont=dict(color=TEXT_MUTED, size=9))),
            hovertemplate="S: %{x:.2f} PSU<br>T: %{y:.2f}°C<extra></extra>",
        ))

    theme_base = {k: v for k, v in PLOTLY_THEME.items() if k not in ['xaxis', 'yaxis']}
    xaxis_custom = {**PLOTLY_THEME["xaxis"], "title": "Salinity (PSU)"}
    yaxis_custom = {**PLOTLY_THEME["yaxis"], "title": "Temperature (°C)"}
    fig.update_layout(
        **theme_base,
        height=280,
        title=dict(text="T–S Diagram" + (f" — WMO {wmo_id}" if wmo_id else " — latest 5,000 levels"),
                   font=dict(color=TEXT_PRIMARY, size=12)),
        xaxis=xaxis_custom,
        yaxis=yaxis_custom,
    )
    return fig


# ── App layout ─────────────────────────────────────────────
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;800&display=swap",
    ],
    title="FloatChat | Ocean Explorer",
    update_title="Exploring…",
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


# ── Styles ─────────────────────────────────────────────────
STYLES = {
    "app": {
        "minHeight": "100vh",
        "background": f"linear-gradient(135deg, {OCEAN_DARK} 0%, #051525 50%, {OCEAN_MID} 100%)",
        "fontFamily": "'Space Mono', monospace",
        "color": TEXT_PRIMARY,
        "overflow": "hidden",
    },
    "header": {
        "background": f"linear-gradient(90deg, {OCEAN_DARK}, {OCEAN_MID})",
        "borderBottom": f"1px solid {OCEAN_BORDER}",
        "padding": "12px 24px",
        "display": "flex",
        "alignItems": "center",
        "gap": "16px",
    },
    "logo": {
        "fontSize": "22px",
        "fontFamily": "'Syne', sans-serif",
        "fontWeight": "800",
        "color": ACCENT_CYAN,
        "letterSpacing": "-0.5px",
    },
    "tagline": {
        "fontSize": "11px",
        "color": TEXT_MUTED,
        "letterSpacing": "2px",
        "textTransform": "uppercase",
        "marginLeft": "4px",
    },
    "sidebar": {
        "width": "340px",
        "minWidth": "340px",
        "background": OCEAN_CARD,
        "borderRight": f"1px solid {OCEAN_BORDER}",
        "display": "flex",
        "flexDirection": "column",
        "height": "calc(100vh - 56px)",
    },
    "chat_box": {
        "flex": "1",
        "overflowY": "auto",
        "padding": "16px",
        "display": "flex",
        "flexDirection": "column",
        "gap": "12px",
    },
    "input_row": {
        "padding": "12px 16px",
        "borderTop": f"1px solid {OCEAN_BORDER}",
        "background": OCEAN_DARK,
        "display": "flex",
        "gap": "8px",
        "alignItems": "center",
    },
    "chat_input": {
        "flex": "1",
        "background": "rgba(0,212,255,0.06)",
        "border": f"1px solid {OCEAN_BORDER}",
        "borderRadius": "8px",
        "color": TEXT_PRIMARY,
        "fontSize": "12px",
        "padding": "10px 14px",
        "fontFamily": "'Space Mono', monospace",
        "outline": "none",
    },
    "send_btn": {
        "background": f"linear-gradient(135deg, {ACCENT_TEAL}, {ACCENT_CYAN})",
        "border": "none",
        "borderRadius": "8px",
        "color": OCEAN_DARK,
        "fontWeight": "700",
        "fontSize": "12px",
        "padding": "10px 18px",
        "cursor": "pointer",
        "fontFamily": "'Space Mono', monospace",
        "whiteSpace": "nowrap",
    },
    "center_panel": {
        "flex": "1",
        "display": "flex",
        "flexDirection": "column",
        "height": "calc(100vh - 56px)",
        "overflow": "hidden",
    },
    "map_header": {
        "padding": "12px 20px 8px",
        "borderBottom": f"1px solid {OCEAN_BORDER}",
        "display": "flex",
        "alignItems": "center",
        "gap": "12px",
        "background": "rgba(4,13,26,0.4)",
    },
    "right_panel": {
        "width": "360px",
        "minWidth": "360px",
        "background": OCEAN_CARD,
        "borderLeft": f"1px solid {OCEAN_BORDER}",
        "display": "flex",
        "flexDirection": "column",
        "height": "calc(100vh - 56px)",
        "overflowY": "auto",
    },
    "card": {
        "background": "rgba(7,26,46,0.8)",
        "border": f"1px solid {OCEAN_BORDER}",
        "borderRadius": "10px",
        "padding": "14px",
        "marginBottom": "12px",
    },
    "stat_badge": {
        "background": "rgba(0,212,255,0.1)",
        "border": f"1px solid {ACCENT_CYAN}33",
        "borderRadius": "6px",
        "padding": "4px 10px",
        "fontSize": "10px",
        "color": ACCENT_CYAN,
        "letterSpacing": "1px",
    },
    "section_label": {
        "fontSize": "9px",
        "fontWeight": "700",
        "color": TEXT_MUTED,
        "letterSpacing": "2px",
        "textTransform": "uppercase",
        "marginBottom": "8px",
    },
    "float_item": {
        "display": "flex",
        "justifyContent": "space-between",
        "alignItems": "center",
        "padding": "8px 12px",
        "borderRadius": "6px",
        "cursor": "pointer",
        "marginBottom": "4px",
        "fontSize": "11px",
        "transition": "background 0.2s",
    },
}


def chat_bubble(text, role="assistant"):
    is_user = role == "user"
    return html.Div([
        html.Div(
            "YOU" if is_user else "FLOAT·AI",
            style={"fontSize": "9px", "fontWeight": "700", "letterSpacing": "2px",
                   "color": ACCENT_WARM if is_user else ACCENT_CYAN,
                   "marginBottom": "4px", "textAlign": "right" if is_user else "left"}
        ),
        html.Div(
            text,
            style={
                "background": f"rgba(240,165,0,0.08)" if is_user else f"rgba(0,212,255,0.06)",
                "border": f"1px solid {'rgba(240,165,0,0.25)' if is_user else 'rgba(0,212,255,0.15)'}",
                "borderRadius": "10px",
                "padding": "10px 14px",
                "fontSize": "12px",
                "lineHeight": "1.6",
                "color": TEXT_PRIMARY,
                "whiteSpace": "pre-wrap",
                "wordBreak": "break-word",
                "marginLeft": "24px" if is_user else "0",
                "marginRight": "0" if is_user else "24px",
            }
        )
    ], style={"display": "flex", "flexDirection": "column",
              "alignItems": "flex-end" if is_user else "flex-start"})


def stat_pill(label, value, color=ACCENT_CYAN):
    return html.Div([
        html.Div(label, style={"fontSize": "9px", "color": TEXT_MUTED, "letterSpacing": "1px"}),
        html.Div(str(value), style={"fontSize": "16px", "fontWeight": "700",
                                     "fontFamily": "'Syne', sans-serif", "color": color}),
    ], style={"background": f"rgba(0,0,0,0.3)", "border": f"1px solid {OCEAN_BORDER}",
              "borderRadius": "8px", "padding": "10px 14px", "flex": "1"})


# ── Layout ──────────────────────────────────────────────────
app.layout = html.Div([
    # State stores
    dcc.Store(id="chat-history", data=[]),
    dcc.Store(id="selected-profile", data=None),
    dcc.Store(id="selected-wmo", data=None),
    dcc.Store(id="all-profiles", data=[]),
    dcc.Store(id="chat-selection", data=None),

    # Header
    html.Div([
        html.Div("⬡ FloatChat", style=STYLES["logo"]),
        html.Div("ARGO OCEAN INTELLIGENCE PLATFORM", style=STYLES["tagline"]),
        html.Div(style={"flex": "1"}),
        html.Div(id="db-status", style={"fontSize": "10px", "color": TEXT_MUTED}),
    ], style=STYLES["header"]),

    # Body
    html.Div([

        # ── Left: Chat sidebar ──────────────────────────────
        html.Div([
            # Float selector
            html.Div([
                html.Div("FLOAT CATALOGUE", style=STYLES["section_label"]),
                html.Div(id="float-list-panel", style={"maxHeight": "180px", "overflowY": "auto"}),
            ], style={"padding": "14px 16px", "borderBottom": f"1px solid {OCEAN_BORDER}"}),

            # Chat messages
            html.Div(id="chat-messages", style=STYLES["chat_box"], children=[
                chat_bubble(
                    "Explore the ocean through real ARGO float measurements.\n\n"
                    "Try asking:\n"
                    "• Show salinity profiles near 12°N 72°E\n"
                    "• List floats from INCOIS\n"
                    "• Show profile 1\n\n"
                    + ("Open-weight AI is enabled." if ai_enabled() else "AI is off. The map and profile explorer are available."),
                    role="assistant"
                )
            ]),

            # Input row
            html.Div([
                dcc.Input(
                    id="chat-input",
                    type="text",
                    placeholder="Ask about ocean data...",
                    debounce=False,
                    n_submit=0,
                    maxLength=MAX_QUERY_CHARS,
                    style=STYLES["chat_input"],
                ),
                html.Button("SEND", id="send-btn", n_clicks=0, style=STYLES["send_btn"]),
            ], style=STYLES["input_row"]),

            dcc.Loading(html.Div(id="agent-loading"), type="dot",
                        color=ACCENT_CYAN, style={"height": "4px"}),

        ], style=STYLES["sidebar"], className="sidebar"),

        # ── Center: Map ─────────────────────────────────────
        html.Div([
            html.Div([
                html.Div("FLOAT TRAJECTORIES & PROFILE LOCATIONS", style=STYLES["section_label"]),
                html.Div(id="map-stats", style={"display": "flex", "gap": "8px"}),
                html.Div(style={"flex": "1"}),
                html.Div([
                    html.Div("FILTER", style={**STYLES["section_label"], "marginBottom": "0", "marginRight": "8px"}),
                    dcc.Dropdown(
                        id="date-filter",
                        options=[
                            {"label": "All time",     "value": "all"},
                            {"label": "Latest data: 30 days", "value": "30"},
                            {"label": "Latest data: 90 days", "value": "90"},
                            {"label": "Latest data: 180 days","value": "180"},
                            {"label": "Latest data: 1 year",    "value": "365"},
                        ],
                        value="all",
                        clearable=False,
                        style={"width": "190px", "fontSize": "11px",
                               "background": OCEAN_CARD, "color": TEXT_PRIMARY},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),
            ], style=STYLES["map_header"]),

            dcc.Graph(id="main-map", style={"flex": "1"},
                      config={"scrollZoom": True, "displayModeBar": False}),
        ], style=STYLES["center_panel"], className="center-panel"),

        # ── Right: Profile charts ────────────────────────────
        html.Div([
            # Stats
            html.Div([
                html.Div("PROFILE INSPECTOR", style=STYLES["section_label"]),
                html.Div(id="profile-meta", style={"fontSize": "11px", "color": TEXT_MUTED,
                                                    "marginBottom": "10px"}),
                html.Div(id="profile-stats-row", style={"display": "flex", "gap": "8px",
                                                          "marginBottom": "12px"}),
            ], style={"padding": "14px 16px", "borderBottom": f"1px solid {OCEAN_BORDER}"}),

            # Charts
            html.Div([
                dcc.Graph(id="ts-profile-chart", config={"displayModeBar": False}),
                dcc.Graph(id="bgc-chart",        config={"displayModeBar": False}),
                dcc.Graph(id="ts-diagram",       config={"displayModeBar": False}),
            ], style={"padding": "12px 8px", "overflowY": "auto"}),

        ], style=STYLES["right_panel"], className="right-panel"),

    ], style={"display": "flex", "flex": "1"}, className="dashboard-body"),

], style=STYLES["app"])


# ── Callbacks ───────────────────────────────────────────────

@app.callback(
    Output("float-list-panel", "children"),
    Output("all-profiles", "data"),
    Output("db-status", "children"),
    Input("date-filter", "value"),
)
def load_data(date_filter):
    # Relative to the dataset's latest measurement so historical samples remain useful.
    if date_filter not in ("all", "30", "90", "180", "365"):
        date_filter = "all"
    date_clause = ""
    params = []
    if date_filter != "all":
        date_clause = "AND date_utc >= (SELECT MAX(date_utc) FROM profiles) - (%s * INTERVAL '1 day')"
        params.append(int(date_filter))
    try:
        profiles = db_query(f"""SELECT profile_id, wmo_id, latitude, longitude, date_utc,
            temp_max, psal_max, has_doxy, has_chla, n_levels
            FROM profile_summary WHERE latitude BETWEEN -90 AND 90
            AND longitude BETWEEN -180 AND 180 {date_clause}
            ORDER BY date_utc DESC NULLS LAST, profile_id DESC LIMIT 2000""", params)
        floats = get_float_list()
    except Exception as exc:
        log.warning("Data unavailable (%s)", type(exc).__name__)
        return html.Div("Database unavailable. Check configuration and schema."), [], "DATABASE UNAVAILABLE"

    float_items = []
    for f in floats[:15]:
        float_items.append(
            html.Div([
                html.Div([
                    html.Span(f["wmo_id"], style={"color": ACCENT_CYAN, "fontWeight": "700"}),
                    html.Span(f" · {f['dac'] or '—'}", style={"color": TEXT_MUTED}),
                ]),
                html.Span(f"{f['n_profiles']} profiles",
                          style={"color": TEXT_MUTED, "fontSize": "10px"}),
            ],
            id={"type": "float-item", "index": f["wmo_id"]},
            style={**STYLES["float_item"],
                   "background": "rgba(0,212,255,0.05)",
                   "border": f"1px solid {OCEAN_BORDER}"},
            n_clicks=0,
        ))

    n_floats = len(set(p["wmo_id"] for p in profiles))
    status = f"{len(profiles)} mapped profiles · {n_floats} floats (up to 2,000)" if profiles else "No profiles in this date range"
    return float_items, profiles, status


@app.callback(
    Output("main-map", "figure"),
    Output("map-stats", "children"),
    Input("all-profiles", "data"),
    Input("selected-wmo", "data"),
)
def update_map(profiles, selected_wmo):
    try:
        fig = build_map(profiles or [], selected_wmo)
    except Exception:
        fig = build_map(profiles or [])
    n   = len(profiles or [])
    wmos = len(set(p["wmo_id"] for p in (profiles or [])))
    stats = [
        html.Span(f"{n} profiles", style=STYLES["stat_badge"]),
        html.Span(f"{wmos} floats", style=STYLES["stat_badge"]),
    ]
    if selected_wmo:
        stats.append(html.Span(f"WMO {selected_wmo}", style={
            **STYLES["stat_badge"],
            "background": f"rgba(0,180,160,0.15)",
            "borderColor": f"{ACCENT_TEAL}66",
            "color": ACCENT_TEAL,
        }))
    return fig, stats


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
    Output("ts-profile-chart", "figure"),
    Output("bgc-chart",        "figure"),
    Output("ts-diagram",       "figure"),
    Output("profile-meta",     "children"),
    Output("profile-stats-row","children"),
    Input("selected-profile", "data"),
    Input("selected-wmo",     "data"),
)
def update_profile_charts(profile_id, wmo_id):
    try:
        obs = get_profile_obs(profile_id) if profile_id else []
        ts_diag = build_ts_diagram(wmo_id)
    except Exception:
        blank = go.Figure().update_layout(**PLOTLY_THEME, title="Database unavailable")
        return blank, blank, blank, "Database unavailable", []
    ts_fig  = build_profile_chart(profile_id, obs) if profile_id else go.Figure().update_layout(
        **PLOTLY_THEME, height=320,
        title=dict(text="Click a profile on the map", font=dict(color=TEXT_MUTED, size=12)))
    bgc_fig = build_bgc_chart(profile_id, obs) if profile_id else go.Figure().update_layout(
        **PLOTLY_THEME, height=260,
        title=dict(text="BGC parameters — select a profile", font=dict(color=TEXT_MUTED, size=12)))

    meta = f"WMO {wmo_id} · Profile #{profile_id} · up to 5,000 levels" if profile_id else "Select a profile on the map"

    stats_row = []
    if profile_id:
        if obs:
            temps = [o["temp"] for o in obs if o["temp"] is not None]
            psals = [o["psal"] for o in obs if o["psal"] is not None]
            pres  = [o["pres"] for o in obs if o["pres"]  is not None]
            if temps:
                stats_row.append(stat_pill("MAX TEMP", f"{max(temps):.1f}°C", ACCENT_WARM))
            if psals:
                stats_row.append(stat_pill("AVG SAL", f"{sum(psals)/len(psals):.2f}", ACCENT_CYAN))
            if pres:
                stats_row.append(stat_pill("MAX PRESSURE", f"{max(pres):.0f} dbar", ACCENT_TEAL))

    return ts_fig, bgc_fig, ts_diag, meta, stats_row


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
    prevent_initial_call=True,
    running=[(Output("send-btn", "disabled"), True, False), (Output("chat-input", "disabled"), True, False)],
)
def handle_chat(n_clicks, n_submit, query, messages, history):
    if not query or not query.strip():
        return no_update, no_update, no_update, no_update, no_update

    messages = (messages or [])[-12:]
    history = (history or [])[-6:]

    # Add user bubble
    messages = messages + [chat_bubble(query, role="user")]

    response, selection = answer_question(query, history, request.remote_addr or "unknown")

    messages = messages + [chat_bubble(response, role="assistant")]

    # Update LangChain history (simple pairs)
    history = history + [
        {"role": "human", "content": query},
        {"role": "assistant", "content": response},
    ]

    return messages[-12:], history[-6:], "", "", selection or no_update


if __name__ == "__main__":
    app.run(debug=False, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8050")))
