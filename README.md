# FloatChat: Argo ocean data explorer

A web dashboard for exploring Argo float profiles from the Indian Ocean: a full-screen map,
a profile inspector, region averages, and an AI assistant that answers from the database
without ever writing SQL.

## Features

- **Map** of every profile (each place a float surfaced and measured the water column). Click
  a dot to inspect it; pick a float to see its trajectory. Filter by period.
- **Profile inspector**: temperature and salinity against pressure, biogeochemistry (oxygen,
  chlorophyll-a, nitrate, backscatter) when measured, and a temperature–salinity diagram.
- **Charts from any question**: a statistic (mean, median, min, max, standard deviation, count,
  percentiles) of temperature, salinity, oxygen, chlorophyll-a, nitrate, backscatter or profile
  counts, grouped by time, season, depth, float or latitude/longitude band, for a named region
  (equator, Arabian Sea, Bay of Bengal, approximate ocean boxes) or a box, comparing up to four
  regions, floats or years — as a line, bar, depth profile, histogram, scatter or map.
- **Honest answers**: when the data can't answer, it says why, e.g. *"No profiles in the Pacific
  Ocean (approximate box) at all. This database has 20 floats in the Indian Ocean area…"*.
- **Analyze tab** (no AI needed): every chart option from dropdowns, plus a map highlight.
- **Ask AI**: plain-English questions. The model picks one of nine fixed, validated read-only
  operations and fills in its arguments (it never writes SQL); Python runs it; the model explains
  the result. Answers can select a profile, highlight a region on the map, or open a chart.
- **Open-weight models only**: OpenRouter (visitors can paste their own key), Amazon Bedrock
  (Qwen3 32B) or a local Ollama. No OpenAI dependency.

## Quick start (Docker)

Needs Docker with Compose 2.24.4 or later.

```bash
cp env.example .env
# Edit .env: set two different long random values for POSTGRES_PASSWORD and APP_DB_PASSWORD.
docker compose up --build -d --wait
```

Open <http://localhost:8080>. The database starts empty; load some floats:

```bash
python download_argo.py --wmo 2902115 --type Sprof --out-dir data/argo
docker compose run --rm etl --dir /data/argo --limit 20
docker compose run --rm etl --dir /data/argo
```

## Quick start (native Python 3.12)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
cp env.example .env   # set DB_* (read-only role) and DB_WRITE_* (importer)
python argo_etl.py --dir data/argo --init-schema
python app.py         # http://127.0.0.1:8050
```

You need a PostgreSQL database named `floatchat`. Docker creates the read-only
`floatchat_reader` role for you. Natively, create it with the SQL in
`deploy/postgres/20-reader.sh`, or point `DB_USER` at an existing user (sessions are read-only
either way).

## Loading data

- `download_argo.py` fetches NetCDF files from the IFREMER GDAC. `--sample` gets 7 curated
  Indian Ocean floats; `--wmo …` gets specific floats; `--type Sprof` gets one merged file per
  BGC float (recommended).
- `argo_etl.py` imports them with QC filtering (flags 1 and 2 only), adjusted values for
  delayed-mode data, per-file transactions, and safe re-imports. It refreshes the
  `profile_summary` materialized view at the end.
- For any one float, import **either** its `Sprof` file **or** its core + BGC files, never both.

## Turning on the AI

| Option | Setup |
| --- | --- |
| Visitor's own OpenRouter key | Nothing on the server. Open **Ask AI → AI settings** and paste a key from [openrouter.ai/keys](https://openrouter.ai/keys). |
| Server-paid OpenRouter | `AI_ENABLED=true`, `AI_PROVIDER=openrouter`, `OPENROUTER_API_KEY=sk-or-…` |
| Amazon Bedrock | `AI_ENABLED=true`, `AI_PROVIDER=bedrock`, `AWS_BEARER_TOKEN_BEDROCK=…` |
| Local Ollama | `ollama pull qwen3:4b`, then `AI_ENABLED=true`, `AI_PROVIDER=ollama` (about 8 GB free RAM) |

Requests are rate-limited (`AI_DAILY_REQUEST_LIMIT`, `AI_HOURLY_CLIENT_LIMIT`,
`AI_USER_KEY_HOURLY_LIMIT`). Every setting is documented in `env.example`.

## Example questions

- "Summarise the dataset"
- "List floats from INCOIS"
- "Profiles near 12°N 72°E"
- "Highlight all the floats on the equator"
- "Graph the average temperature of floats on the equator from March to June"
- "Average salinity by depth in the Arabian Sea in 2024"
- "Compare oxygen in the Arabian Sea and Bay of Bengal by month"
- "Maximum chlorophyll per year"
- "Histogram of surface temperatures in 2024"
- "Temperature against salinity on the equator"
- "Mean temperature in the Pacific Ocean" (answers that there is no Pacific data, and what there is)

## Repository layout

```
app.py              Dash dashboard: map, inspector, Analyze tab, chat, Analysis panel
chat_service.py     AI pipeline: providers, quotas, plan (+1 repair) → tool → answer → dashboard actions
ocean_tools.py      The validated read-only operations (tool registry)
analysis.py         Analysis engine: any statistic/grouping/comparison → safe SQL → result + diagnosis
charts.py           Figures and captions for every analysis chart type
regions.py          Named regions (incl. approximate oceans) and the shared region filter
theme.py            Design tokens and shared Plotly styling
database.py         Read-only, time-limited, row-capped PostgreSQL access
argo_etl.py         NetCDF → PostgreSQL importer
download_argo.py    Argo file downloader
postgres_mcp.py     Optional MCP server exposing the same operations
schema.sql          Tables, indexes and the materialized profile summary
assets/style.css    Styling
deploy/             Caddy, production Compose override, Lightsail scripts
docs/               Project guide and AWS deployment guide
tests/              unittest suites (set TEST_DATABASE_URL to a *_test database for integration tests)
```

## Documentation

- [docs/project-guide.md](docs/project-guide.md): how every part works, configuration
  reference, and where to make common changes.
- [docs/aws-deployment.md](docs/aws-deployment.md): hosting on AWS Lightsail with HTTPS, costs,
  backups and adding more floats.

## Tests

```bash
python -m unittest discover -s tests -v
```

Integration tests run only when `TEST_DATABASE_URL` points at a disposable database whose name
ends in `_test`. CI runs everything against PostgreSQL 16 and boots the Docker stack.

## License

General Public License

## Acknowledgments

- Argo float data from the international Argo program and its data assembly centres
  ([argo.ucsd.edu](https://argo.ucsd.edu/))
- INCOIS (Indian National Centre for Ocean Information Services) for regional deployments
