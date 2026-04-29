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

import os, json, asyncio, re
from datetime import datetime, timedelta
import nest_asyncio

import dash
from dash import Dash, dcc, html, Input, Output, State, ctx, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.express as px
import psycopg2, psycopg2.extras
from dotenv import load_dotenv

# Apply nest_asyncio to allow async operations in Dash callbacks
nest_asyncio.apply()

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

load_dotenv()

# ── Config ─────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_CONFIG = dict(
    dbname=os.getenv("DB_NAME", "floatchat"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASS", ""),
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5432"),
)

PYTHON_PATH = os.getenv("PYTHON_PATH", "python")
MCP_SCRIPT  = os.path.join(os.path.dirname(__file__), "mcp", "postgres_mcp.py")

server_params = StdioServerParameters(command=PYTHON_PATH, args=[MCP_SCRIPT])

model = ChatOpenAI(model="gpt-4o", openai_api_key=OPENAI_API_KEY, temperature=0)

SYSTEM_PROMPT = """You are FloatChat, an expert oceanographic data assistant for the ARGO float program.
You have access to a PostgreSQL database containing ARGO float profiles from the Indian Ocean.

Database schema:
- floats(wmo_id, platform_type, dac, project_name, pi_name)
- profiles(profile_id, wmo_id, cycle_number, direction, latitude, longitude, date_utc, data_mode)
- observations(obs_id, profile_id, wmo_id, date_utc, latitude, longitude, depth_level,
               pres, temp, psal, doxy, chla, nitrate, bbp700)
- profile_summary VIEW: aggregated stats per profile

You can answer questions like:
- "Show salinity profiles near the Arabian Sea in March 2023"
- "What are the nearest floats to 12°N 72°E?"
- "Compare BGC parameters in the Bay of Bengal for the last 6 months"
- "List all floats from INCOIS"

Always return structured data when asked for profiles or trajectories so the dashboard can visualize them.
When returning profile data for visualization, include a JSON block like:
{"viz_type": "profile", "profile_id": 123}
{"viz_type": "trajectory", "wmo_id": "2902115"}
{"viz_type": "map", "profiles": [...]}
"""

agent_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("placeholder", "{messages}"),
])

# ── DB helpers ─────────────────────────────────────────────
def db_query(sql, params=None):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()

def get_all_profiles(limit=2000):
    return db_query("""
        SELECT profile_id, wmo_id, latitude, longitude, date_utc,
               temp_max, psal_max, has_doxy, has_chla, n_levels
        FROM profile_summary
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY date_utc DESC LIMIT %s
    """, [limit])

def get_profile_obs(profile_id):
    return db_query("""
        SELECT pres, temp, psal, doxy, chla, nitrate, bbp700
        FROM observations WHERE profile_id=%s ORDER BY pres ASC
    """, [profile_id])

def get_trajectory(wmo_id):
    return db_query("""
        SELECT cycle_number, date_utc, latitude, longitude
        FROM profiles WHERE wmo_id=%s AND latitude IS NOT NULL
        ORDER BY date_utc ASC
    """, [wmo_id])

def get_float_list():
    return db_query("""
        SELECT f.wmo_id, f.dac, COUNT(p.profile_id) n_profiles,
               MIN(p.date_utc) first_date, MAX(p.date_utc) last_date
        FROM floats f LEFT JOIN profiles p ON p.wmo_id=f.wmo_id
        GROUP BY f.wmo_id, f.dac ORDER BY n_profiles DESC LIMIT 50
    """)

# ── Agent ──────────────────────────────────────────────────
async def run_agent(query, history):
    """
    Run the AI agent with database tools to respond to user queries about ARGO data.
    """
    tools = []
    try:
        # Initialize MCP session and load tools
        print(f"[Agent] Initializing MCP client...")
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                print(f"[Agent] Loaded {len(tools)} tools: {[t.name for t in tools]}")
                
                # Create agent with tools
                agent = create_react_agent(
                    model, 
                    tools,
                    state_modifier=SYSTEM_PROMPT,
                    debug=True
                )
                
                # Build message history
                messages = []
                for m in history:
                    if m["role"] == "human":
                        messages.append(HumanMessage(content=m["content"]))
                    elif m["role"] == "assistant":
                        messages.append(AIMessage(content=m["content"]))
                messages.append(HumanMessage(content=query))
                
                # Run agent
                print(f"[Agent] Running agent with {len(messages)} messages...")
                result = await agent.ainvoke({"messages": messages})
                
                # Extract response
                if isinstance(result, dict) and "messages" in result:
                    final_msg = result["messages"][-1]
                    if hasattr(final_msg, 'content'):
                        return final_msg.content
                
                return str(result)
                    
    except Exception as e:
        print(f"[Agent] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        
        # Try fallback: query database directly
        try:
            print(f"[Agent] Attempting direct database query fallback...")
            if "floater" in query.lower() or "float" in query.lower():
                floats = get_float_list()
                if floats:
                    float_names = ", ".join([f"{f['wmo_id']} ({f['n_profiles']} profiles)" for f in floats[:10]])
                    return f"Here are the active floats in the database:\n\n{float_names}\n\n(showing top 10 of {len(floats)} floats)"
        except Exception as fallback_err:
            print(f"[Agent] Fallback failed: {fallback_err}")
        
        return f"Error processing your query. Please try again or rephrase your question."

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
            mapbox=dict(style="carto-darkmatter", center=dict(lat=15, lon=72), zoom=3),
            height=480,
        )
        return fig

    lats  = [p["latitude"]  for p in profiles]
    lons  = [p["longitude"] for p in profiles]
    wmos  = [p["wmo_id"]    for p in profiles]
    dates = [str(p["date_utc"])[:10] if p["date_utc"] else "" for p in profiles]
    temps = [p.get("temp_max") or 0 for p in profiles]
    texts = [f"WMO: {w}<br>Date: {d}<br>T_max: {t:.1f}°C" if t else f"WMO: {w}<br>Date: {d}"
             for w, d, t in zip(wmos, dates, temps)]

    fig.add_trace(go.Scattermapbox(
        lat=lats, lon=lons, mode="markers",
        marker=dict(size=7, color=temps, colorscale="Thermal",
                    colorbar=dict(title="T max °C", thickness=10, len=0.5,
                                  tickfont=dict(color=TEXT_MUTED, size=9)),
                    opacity=0.85),
        text=texts, hoverinfo="text",
        customdata=[p["profile_id"] for p in profiles],
        name="Profiles",
    ))

    # Highlight selected float trajectory
    if selected_wmo:
        traj = get_trajectory(selected_wmo)
        if traj:
            fig.add_trace(go.Scattermapbox(
                lat=[t["latitude"] for t in traj],
                lon=[t["longitude"] for t in traj],
                mode="lines+markers",
                line=dict(width=2, color=ACCENT_CYAN),
                marker=dict(size=5, color=ACCENT_CYAN),
                name=f"WMO {selected_wmo}",
            ))

    fig.update_layout(
        **PLOTLY_THEME,
        mapbox=dict(style="carto-darkmatter",
                    center=dict(lat=sum(lats)/len(lats), lon=sum(lons)/len(lons)),
                    zoom=3),
        height=480,
        showlegend=bool(selected_wmo),
        legend=dict(bgcolor="rgba(10,34,64,0.8)", bordercolor=OCEAN_BORDER,
                    font=dict(color=TEXT_PRIMARY, size=10)),
    )
    return fig


def build_profile_chart(profile_id):
    obs = get_profile_obs(profile_id)
    if not obs:
        return go.Figure().update_layout(**PLOTLY_THEME, height=320,
            title=dict(text="No observations found", font=dict(color=TEXT_MUTED)))

    pres  = [o["pres"]  for o in obs if o["pres"]  is not None]
    temps = [o["temp"]  for o in obs if o["pres"]  is not None]
    psals = [o["psal"]  for o in obs if o["pres"]  is not None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=temps, y=pres, mode="lines+markers",
        name="Temperature (°C)", line=dict(color=ACCENT_WARM, width=2),
        marker=dict(size=4, color=ACCENT_WARM)))
    fig.add_trace(go.Scatter(x=psals, y=pres, mode="lines+markers",
        name="Salinity (PSU)", line=dict(color=ACCENT_CYAN, width=2),
        marker=dict(size=4, color=ACCENT_CYAN),
        xaxis="x2"))

    theme_base = {k: v for k, v in PLOTLY_THEME.items() if k not in ['xaxis', 'yaxis']}
    xaxis_custom = {**PLOTLY_THEME["xaxis"], "title": "Temperature °C", "color": ACCENT_WARM}
    yaxis_custom = {**PLOTLY_THEME["yaxis"], "autorange": "reversed", "title": "Pressure (dbar)"}
    fig.update_layout(
        **theme_base,
        height=320,
        title=dict(text=f"Profile #{profile_id} — T/S vs Depth",
                   font=dict(color=TEXT_PRIMARY, size=12)),
        yaxis=yaxis_custom,
        xaxis=xaxis_custom,
        xaxis2=dict(title="Salinity PSU", color=ACCENT_CYAN,
                    overlaying="x", side="top", gridcolor="rgba(0,0,0,0)"),
        legend=dict(bgcolor="rgba(10,34,64,0.7)", bordercolor=OCEAN_BORDER,
                    font=dict(color=TEXT_PRIMARY, size=10), x=0.65, y=0.98),
    )
    return fig


def build_bgc_chart(profile_id):
    obs = get_profile_obs(profile_id)
    bgc_vars = [("doxy", "#00d4ff", "O₂ (μmol/kg)"),
                ("chla", "#00ff9d", "Chl-a (mg/m³)"),
                ("nitrate", "#f0a500", "NO₃ (μmol/kg)")]

    fig = go.Figure()
    has_any = False
    for key, color, label in bgc_vars:
        vals = [o.get(key) for o in obs]
        pres = [o["pres"] for o in obs]
        valid = [(p, v) for p, v in zip(pres, vals) if p is not None and v is not None]
        if valid:
            has_any = True
            px_vals, vx_vals = zip(*valid)
            fig.add_trace(go.Scatter(x=vx_vals, y=px_vals, mode="lines",
                name=label, line=dict(color=color, width=2)))

    if not has_any:
        fig.add_annotation(text="No BGC data for this profile",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
            font=dict(color=TEXT_MUTED, size=13))

    theme_base = {k: v for k, v in PLOTLY_THEME.items() if k not in ['xaxis', 'yaxis']}
    xaxis_custom = {**PLOTLY_THEME["xaxis"], "title": "Concentration"}
    yaxis_custom = {**PLOTLY_THEME["yaxis"], "autorange": "reversed", "title": "Pressure (dbar)"}
    fig.update_layout(
        **theme_base,
        height=280,
        title=dict(text=f"Profile #{profile_id} — BGC Parameters",
                   font=dict(color=TEXT_PRIMARY, size=12)),
        yaxis=yaxis_custom,
        xaxis=xaxis_custom,
        legend=dict(bgcolor="rgba(10,34,64,0.7)", bordercolor=OCEAN_BORDER,
                    font=dict(color=TEXT_PRIMARY, size=10)),
    )
    return fig


def build_ts_diagram(wmo_id=None):
    """Temperature–Salinity scatter diagram."""
    sql = """
        SELECT o.temp, o.psal, o.pres, o.wmo_id
        FROM observations o
        WHERE o.temp IS NOT NULL AND o.psal IS NOT NULL
    """
    params = []
    if wmo_id:
        sql += " AND o.wmo_id = %s"
        params.append(wmo_id)
    sql += " LIMIT 5000"
    rows = db_query(sql, params or None)

    fig = go.Figure()
    if rows:
        fig.add_trace(go.Scatter(
            x=[r["psal"] for r in rows],
            y=[r["temp"] for r in rows],
            mode="markers",
            marker=dict(size=3, color=[r["pres"] for r in rows],
                        colorscale="Viridis", opacity=0.6,
                        colorbar=dict(title="Depth (dbar)", thickness=10, len=0.6,
                                      tickfont=dict(color=TEXT_MUTED, size=9))),
            hovertemplate="S: %{x:.2f} PSU<br>T: %{y:.2f}°C<extra></extra>",
        ))

    theme_base = {k: v for k, v in PLOTLY_THEME.items() if k not in ['xaxis', 'yaxis']}
    xaxis_custom = {**PLOTLY_THEME["xaxis"], "title": "Salinity (PSU)"}
    yaxis_custom = {**PLOTLY_THEME["yaxis"], "title": "Temperature (°C)"}
    fig.update_layout(
        **theme_base,
        height=280,
        title=dict(text="T–S Diagram" + (f" — WMO {wmo_id}" if wmo_id else " — All Floats"),
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
    suppress_callback_exceptions=True,
)
server = app.server

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
                html.Div("ACTIVE FLOATS", style=STYLES["section_label"]),
                html.Div(id="float-list-panel", style={"maxHeight": "180px", "overflowY": "auto"}),
            ], style={"padding": "14px 16px", "borderBottom": f"1px solid {OCEAN_BORDER}"}),

            # Chat messages
            html.Div(id="chat-messages", style=STYLES["chat_box"], children=[
                chat_bubble(
                    "Hello! I'm FloatChat — your AI guide to ARGO ocean data.\n\n"
                    "Try asking:\n"
                    "• Show salinity profiles near 12°N 72°E\n"
                    "• List floats from INCOIS\n"
                    "• Compare BGC data in the Arabian Sea",
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
                    style=STYLES["chat_input"],
                ),
                html.Button("SEND", id="send-btn", n_clicks=0, style=STYLES["send_btn"]),
            ], style=STYLES["input_row"]),

            dcc.Loading(html.Div(id="agent-loading"), type="dot",
                        color=ACCENT_CYAN, style={"height": "4px"}),

        ], style=STYLES["sidebar"]),

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
                            {"label": "Last 30 days", "value": "30"},
                            {"label": "Last 90 days", "value": "90"},
                            {"label": "Last 180 days","value": "180"},
                            {"label": "Last year",    "value": "365"},
                        ],
                        value="all",
                        clearable=False,
                        style={"width": "150px", "fontSize": "11px",
                               "background": OCEAN_CARD, "color": TEXT_PRIMARY},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),
            ], style=STYLES["map_header"]),

            dcc.Graph(id="main-map", style={"flex": "1"},
                      config={"scrollZoom": True, "displayModeBar": False}),
        ], style=STYLES["center_panel"]),

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

        ], style=STYLES["right_panel"]),

    ], style={"display": "flex", "flex": "1"}),

], style=STYLES["app"])


# ── Callbacks ───────────────────────────────────────────────

@app.callback(
    Output("float-list-panel", "children"),
    Output("all-profiles", "data"),
    Output("db-status", "children"),
    Input("date-filter", "value"),
)
def load_data(date_filter):
    # Load profiles filtered by date
    if date_filter == "all":
        date_clause = ""
        params = [2000]
    else:
        days = int(date_filter)
        since = datetime.utcnow() - timedelta(days=days)
        date_clause = f"WHERE date_utc >= '{since.date()}'"
        params = [2000]

    profiles = db_query(f"""
        SELECT profile_id, wmo_id, latitude, longitude, date_utc,
               temp_max, psal_max, has_doxy, has_chla, n_levels
        FROM profile_summary {date_clause}
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY date_utc DESC LIMIT 2000
    """)

    floats = get_float_list()
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
    status = f"● LIVE  {len(profiles)} profiles · {n_floats} floats"
    return float_items, profiles, status


@app.callback(
    Output("main-map", "figure"),
    Output("map-stats", "children"),
    Input("all-profiles", "data"),
    Input("selected-wmo", "data"),
)
def update_map(profiles, selected_wmo):
    fig = build_map(profiles or [], selected_wmo)
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
    prevent_initial_call=True,
)
def on_map_click(click_data):
    if not click_data:
        return no_update, no_update
    point = click_data["points"][0]
    profile_id = point.get("customdata")
    # Try to get WMO from text
    text = point.get("text", "")
    wmo = None
    m = re.search(r"WMO: (\w+)", text)
    if m:
        wmo = m.group(1)
    return profile_id, wmo


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
    ts_fig  = build_profile_chart(profile_id) if profile_id else go.Figure().update_layout(
        **PLOTLY_THEME, height=320,
        title=dict(text="Click a profile on the map", font=dict(color=TEXT_MUTED, size=12)))
    bgc_fig = build_bgc_chart(profile_id) if profile_id else go.Figure().update_layout(
        **PLOTLY_THEME, height=260,
        title=dict(text="BGC parameters — select a profile", font=dict(color=TEXT_MUTED, size=12)))
    ts_diag = build_ts_diagram(wmo_id)

    meta = f"WMO {wmo_id} · Profile #{profile_id}" if profile_id else "Select a profile on the map"

    stats_row = []
    if profile_id:
        obs = get_profile_obs(profile_id)
        if obs:
            temps = [o["temp"] for o in obs if o["temp"] is not None]
            psals = [o["psal"] for o in obs if o["psal"] is not None]
            pres  = [o["pres"] for o in obs if o["pres"]  is not None]
            has_bgc = any(o.get("doxy") for o in obs)
            if temps:
                stats_row.append(stat_pill("MAX TEMP", f"{max(temps):.1f}°C", ACCENT_WARM))
            if psals:
                stats_row.append(stat_pill("AVG SAL", f"{sum(psals)/len(psals):.2f}", ACCENT_CYAN))
            if pres:
                stats_row.append(stat_pill("MAX DEPTH", f"{max(pres):.0f}m", ACCENT_TEAL))

    return ts_fig, bgc_fig, ts_diag, meta, stats_row


@app.callback(
    Output("chat-messages", "children"),
    Output("chat-history",  "data"),
    Output("chat-input",    "value"),
    Output("agent-loading", "children"),
    Input("send-btn",   "n_clicks"),
    Input("chat-input", "n_submit"),
    State("chat-input",    "value"),
    State("chat-messages", "children"),
    State("chat-history",  "data"),
    prevent_initial_call=True,
)
def handle_chat(n_clicks, n_submit, query, messages, history):
    if not query or not query.strip():
        return no_update, no_update, no_update, no_update

    messages = messages or []
    history  = history  or []

    # Add user bubble
    messages = messages + [chat_bubble(query, role="user")]

    # Run agent
    try:
        response = asyncio.run(run_agent(query, history))
    except Exception as e:
        response = f"Error: {e}"

    messages = messages + [chat_bubble(response, role="assistant")]

    # Update LangChain history (simple pairs)
    history = history + [
        {"role": "human", "content": query},
        {"role": "assistant", "content": response},
    ]

    return messages, history, "", ""


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
