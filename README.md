# FloatChat — AI-Powered ARGO Ocean Data Dashboard

An intelligent oceanographic data exploration platform combining natural language AI with interactive geospatial visualization of ARGO float profiles from the Indian Ocean.

## Overview

FloatChat is a Dash-based web application that provides:

- **🤖 AI Chat Interface**: Ask natural language questions about ARGO float data and get intelligent responses
- **🗺️ Interactive Map**: Visualize float trajectories and profile locations with temperature color-coding
- **📊 Profile Inspector**: View detailed T/S (temperature-salinity) diagrams, BGC parameters, and depth profiles
- **🌊 Oceanographic Data**: Access ARGO float profiles including temperature, salinity, dissolved oxygen, chlorophyll-a, and other biogeochemical parameters

## Architecture

```
Left Sidebar          Center Panel          Right Panel
(Chat + Floats)      (Interactive Map)     (Profile Charts)
    ↓                      ↓                    ↓
User Query → AI Agent (LLM + MCP Tools) → PostgreSQL Database
    ↑                      ↓
    └──────────────────────┴──────────────────────┘
              Response & Visualizations
```

### Components

- **Frontend**: Dash/Plotly for responsive UI with dark ocean theme
- **Backend**: Python async application with LangChain 1.x and LangGraph
- **Database**: PostgreSQL with ARGO float profiles, observations, and computed aggregates
- **AI/LLM**: OpenAI GPT-4o with Model Context Protocol (MCP) for database access
- **Visualization**: Plotly for maps, charts, and interactive exploration

## Installation

### 1. Clone and Setup

```bash
cd FloatChat
python -m venv .venv
.venv\Scripts\activate  # Windows
# or
source .venv/bin/activate  # Linux/Mac
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Copy `env.example` to `.env` and fill in your values:

```bash
cp env.example .env
```

Edit `.env` with:

```env
# Database
DB_NAME=floatchat
DB_USER=postgres
DB_PASS=your_postgres_password
DB_HOST=localhost
DB_PORT=5432

# OpenAI API
OPENAI_API_KEY=sk-...your-key-here...

# Python path (optional, defaults to 'python')
PYTHON_PATH=python
```

### 4. Database Setup

Ensure PostgreSQL is running and load the schema:

```bash
psql -U postgres -d floatchat -f schema.sql
```

Load ARGO data:

```bash
python argo_etl.py --dir data/argo/incois/FLOAT_ID/profiles/
```

## Running the Application

```bash
python app.py
```

The dashboard will be available at: **http://localhost:8050**

## Usage

### Chat Panel (Left Sidebar)

Ask natural language questions about ARGO data:

- "Show salinity profiles near 12°N 72°E"
- "List all floats from INCOIS with over 50 profiles"
- "What's the maximum temperature in the Arabian Sea?"
- "Compare BGC parameters between different floats"

The AI agent uses database tools to execute queries and return real data.

### Map View (Center)

- **Click profiles** on the map to select and inspect them
- **Filter by date** using the dropdown (Last 30/90/180 days, or all time)
- **Hover** over markers to see float ID, date, and max temperature
- **View float trajectories** when a float is selected (highlighted in cyan)

### Profile Inspector (Right Panel)

When a profile is selected:

- **T/S Chart**: Temperature vs Salinity depth profile with dual axes
- **BGC Chart**: Dissolved oxygen, chlorophyll-a, and nitrate concentrations
- **T–S Diagram**: Temperature-salinity scatter plot for water mass identification
- **Stats**: Maximum temperature, average salinity, and maximum depth

## Project Structure

```
FloatChat/
├── app.py                      # Main Dash application
├── postgres_mcp.py             # MCP server for database access
├── argo_etl.py                 # ETL script for loading ARGO data
├── download_argo.py            # Script to download ARGO data
├── schema.sql                  # PostgreSQL schema
├── requirements.txt            # Python dependencies
├── env.example                 # Environment template
├── README.md                   # This file
├── data/
│   └── argo/
│       └── incois/             # ARGO profiles (NetCDF format)
│           └── 2902115/
│               └── profiles/
│                   ├── BD2902115_001.nc
│                   ├── BD2902115_002.nc
│                   └── ...
└── __pycache__/
```

## Database Schema

### floats
- `wmo_id`: World Meteorological Organization ID (primary key)
- `platform_type`: Type of float platform
- `dac`: Data Assembly Center
- `project_name`: Associated project
- `pi_name`: Principal investigator

### profiles
- `profile_id`: Unique profile identifier
- `wmo_id`: Float WMO ID (foreign key)
- `cycle_number`: Cycle number for this float
- `direction`: Ascending or descending profile
- `latitude`, `longitude`: Location
- `date_utc`: Profile timestamp
- `data_mode`: Real-time or delayed-mode

### observations
- `obs_id`: Observation ID
- `profile_id`: Profile ID (foreign key)
- `pres`: Pressure (dbar)
- `temp`: Temperature (°C)
- `psal`: Practical salinity (PSU)
- `doxy`: Dissolved oxygen (μmol/kg)
- `chla`: Chlorophyll-a (mg/m³)
- `nitrate`: Nitrate (μmol/kg)
- `bbp700`: Backscatter at 700nm

### profile_summary
Materialized view with aggregated statistics per profile:
- Count of observations
- Temperature/salinity extremes
- Presence of BGC parameters

## Configuration

### Theme Customization

Edit color palette in `app.py`:

```python
OCEAN_DARK   = "#040d1a"
OCEAN_MID    = "#071a2e"
OCEAN_CARD   = "#0a2240"
ACCENT_CYAN  = "#00d4ff"
ACCENT_WARM  = "#f0a500"
```

### AI Agent Customization

Modify `SYSTEM_PROMPT` in `app.py` to change agent behavior and capabilities.

### Map Style

Change `mapbox=dict(style="carto-darkmatter", ...)` to other Mapbox styles (e.g., "streets-v11", "satellite-v9").

## Troubleshooting

### Agent Error: "unhandled errors in a TaskGroup"

This usually indicates an issue with MCP tool loading or database connection:

1. Check `.env` file is properly configured
2. Verify PostgreSQL is running: `psql -U postgres -c "SELECT 1"`
3. Check database exists: `psql -l | grep floatchat`
4. Review terminal output for detailed error messages

### No floats appearing on map

1. Verify data is loaded: `psql -U postgres -d floatchat -c "SELECT COUNT(*) FROM profiles"`
2. Check profiles have valid coordinates: `SELECT COUNT(*) FROM profiles WHERE latitude IS NOT NULL`
3. Ensure profile_summary view exists: `psql -U postgres -d floatchat -c "SELECT * FROM profile_summary LIMIT 1"`

### Slow response times

1. Check database indexes: PostgreSQL should auto-index foreign keys
2. Limit data range using date filter
3. Monitor PostgreSQL performance: `SELECT * FROM pg_stat_statements`

## Key Technologies

- **Framework**: [Dash](https://dash.plotly.com/) (Python web framework)
- **Visualization**: [Plotly](https://plotly.com/python/)
- **Database**: PostgreSQL with psycopg2
- **LLM**: OpenAI GPT-4o
- **Agent Framework**: [LangChain](https://www.langchain.com/) + [LangGraph](https://github.com/langchain-ai/langgraph)
- **Async Support**: [nest_asyncio](https://github.com/erdewit/nest_asyncio)
- **DB Access**: Model Context Protocol (MCP) via [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)

## Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_NAME` | PostgreSQL database name | `floatchat` | ✓ |
| `DB_USER` | PostgreSQL user | `postgres` | ✓ |
| `DB_PASS` | PostgreSQL password | `` | ✓ |
| `DB_HOST` | PostgreSQL host | `localhost` | ✓ |
| `DB_PORT` | PostgreSQL port | `5432` | |
| `OPENAI_API_KEY` | OpenAI API key | | ✓ |
| `PYTHON_PATH` | Python executable path | `python` | |

## Performance Tips

1. **Data Loading**: Use date filter to reduce initial map load
2. **Queries**: Ask specific questions (e.g., "floats in Bay of Bengal" vs "all floats")
3. **Database**: Add indexes on commonly filtered columns (date_utc, latitude, longitude)
4. **Caching**: Consider Redis for frequently accessed queries

## Contributing

To extend FloatChat:

1. Add new chat capabilities in `SYSTEM_PROMPT`
2. Create new PostgreSQL tools in `postgres_mcp.py`
3. Add new chart types in `build_*_chart()` functions
4. Modify styling via `STYLES` dictionary and color palette

## License

[Specify your license here]

## Contact

For questions about the ARGO program, visit: https://www.goaargo.ucsd.edu/

## Acknowledgments

- ARGO float data from international oceanographic centers
- INCOIS (Indian National Centre for Ocean Information Services) for regional deployment
- OpenAI for GPT-4o language model
