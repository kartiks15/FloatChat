# Region Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user ask "highlight all the floats on the equator" or "graph the average
temperature of floats on the equator from March to June" and get a map highlight and a chart.

**Architecture:** Two new validated, read-only tools in `ocean_tools.py`
(`region_profiles`, `aggregate_measurements`) share one `RegionFilter` model (latitude/longitude
box or named region, dates, calendar months, float). `chat_service.answer_question` turns a
tool result into **dashboard actions** (`select`, `highlight`, `chart`). `app.py` draws a
highlight layer on the map and an Analysis panel with the chart. The model still never writes
SQL and still makes at most two calls per question.

**Tech Stack:** Python 3.12, Dash 3.4 / Plotly 6.9, Pydantic 2, psycopg2, PostgreSQL 16,
`unittest`.

**Spec:** This document. It is based on the user's request in chat and on
[`docs/project-guide.md`](project-guide.md), which describes the current code.

---

## Why this is impossible today

| Need | What blocks it now |
| --- | --- |
| "All floats on the equator" | The equator is a **latitude band**, but `search_profiles` only searches a **circle** around one point, and returns at most 100 profiles. |
| Highlight them | A chat answer can select **one** profile. The answer prompt also says *never claim a map was changed*. Nothing can highlight a set of profiles. |
| "March to June" | There is no month filter. Dates must be an exact range with a year. |
| Average temperature graph | No tool aggregates anything. The answer prompt forbids computing statistics from a sample. The chat can only return text. |

## Scope

The user widened the scope on 18 October 2026. Everything below is in this plan:

| # | Change | Task |
| --- | --- | --- |
| 1 | Region filters, server-side averages, map highlight, Analysis chart, driven from chat | 1–6 |
| 2 | Re-imports update a profile's `data_mode` instead of keeping a stale one (project guide, gotcha 2) | 7 |
| 3 | Top-bar totals use real counts, not the 500-float list | 8 |
| 4 | A **Regions** tab that highlights and charts without the AI | 9 |
| 5 | The new tools exposed in `postgres_mcp.py` | 10 |
| 6 | Remove the unused `dash-bootstrap-components`; rewrite `README.md` | 11 |
| 7 | End-to-end check with real data; update the project guide | 12 |

---

## Decisions to confirm before starting

Each is my default. Change any of them before execution starts.

| # | Question | Default in this plan |
| --- | --- | --- |
| D1 | How wide is "the equator"? | **5°S–5°N**, all longitudes. The user can still say "within 1° of the equator" and the model passes explicit bounds. |
| D2 | What does "average temperature" mean? | **Mean of per-profile means** inside a pressure layer, so a profile with many levels doesn't count more than one with few. Default layer **0–10 dbar** (surface) for graphs over time. "By depth" gives a mean profile in 50 dbar bins over 0–2,000 dbar. |
| D3 | "March to June" with no year? | Those months in **every year** in the data, one point per calendar month. The answer and chart say which years were included. With a year ("March to June 2024") it's that date range, one point per month. |
| D4 | Where does the chart appear? | A floating **Analysis** panel above the status bar in the map's centre column, with a Close button. Not inside the narrow chat bubble. |
| D5 | Does the highlight follow the Period dropdown? | **No.** The highlight shows its own filter. Its label, in the status bar, says what is highlighted, with a Clear button. |
| D6 | Highlight colour | Amber `#fbbf24`, to stay distinct from the emerald used for the selected float. |

---

## Global Constraints

- Python 3.12. **No new dependencies.** Install from `requirements.lock` only.
- **Never execute model-written SQL.** Any column name or SQL expression put into a query comes
  from a code-side dict keyed by a Pydantic `Literal` value. Every user or model value is a
  bound `%s` parameter.
- All database reads go through `database.fetch_rows` (read-only, at most 5,000 rows).
- Still at most **two model calls** per chat question. Quotas are unchanged.
- Everything put in a `dcc.Store` must be JSON-serialisable (no `date`/`datetime` objects).
- The map's trace order is fixed and referenced only through the `TRACE_*` constants
  (Task 5).
- Colours are defined in both the `app.py` constants and the `assets/style.css` tokens.
- UI copy is sentence case, short, and matches the existing tone.
- Tests use `unittest` (not pytest) and run from the repository root.
- Work on branch `feature/region-analysis`. One commit per task, using the message the task
  gives. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Load the `dataviz` skill before writing the chart code in Task 6.

## Review Focus

These are the failure modes most likely to hit a real user. Each one is pinned by a test in
the task named.

1. **"March to June" with no year** → months 3–6 across all years. The result must report the
   first and last date included, so the answer can name the years. *(Task 3:
   `test_aggregate_month_of_year_across_years`)*
2. **A filter that matches nothing** → no crash. The chart says *No measurements match this
   filter*, no highlight is drawn, and the map is unchanged. *(Task 4:
   `test_actions_skip_empty_highlight`; Task 6: `test_analysis_chart_empty`)*
3. **A box crossing the ±180° line** (`lon_min > lon_max`) → wraps instead of matching
   nothing. *(Task 1: `test_region_conditions_wrap_antimeridian`; Task 2:
   `test_region_profiles_across_antimeridian`)*
4. **Changing the Period or picking a float while a highlight is shown** → the highlight
   stays. *(Task 5: `test_load_data_keeps_highlight`, `test_selection_patch_leaves_highlight`)*
5. **A profile stored as both a core and a BGC grid, each with temperature** → counted as
   one profile. *(Task 3: `test_aggregate_counts_core_and_bgc_grids_once`)*

The 10 s statement timeout on a large database (~2 M observation rows) can't be proven in a
unit test. Task 7 checks it by hand.

---

## Before you start

- [ ] **Create the branch and environment**

```bash
git switch -c feature/region-analysis
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
```

- [ ] **Run the baseline unit tests**

Run: `python -m unittest discover -s tests -v`
Expected: `Ran 39 tests` … `OK (skipped=5)`. If anything fails, stop and report. Don't
build on a red baseline.

- [ ] **Start a disposable test database** (same image and password as CI, on port 55432 so it
  doesn't clash with the local Postgres on 5432)

```bash
docker run -d --rm --name floatchat-test-db -e POSTGRES_DB=floatchat_test \
  -e POSTGRES_PASSWORD=test-only-password -p 127.0.0.1:55432:5432 postgres:16-bookworm
export TEST_DATABASE_URL=postgresql://postgres:test-only-password@localhost:55432/floatchat_test
python -m unittest discover -s tests -v
```

Expected: `Ran 39 tests` … `OK`, with no skips. Keep `TEST_DATABASE_URL` exported for every
later step.

---

### Task 1: `RegionFilter` and its SQL conditions

**Files:**
- Modify: `ocean_tools.py` (add after `SearchArgs`)
- Test: `tests/test_regions.py` (new)

**Interfaces:**
- Produces:
  - `REGIONS: dict[str, dict]`, which maps `"equator"`, `"arabian_sea"` and `"bay_of_bengal"`
    to `{"lat_min", "lat_max", "lon_min", "lon_max"}`. Values: equator `-5, 5, -180, 180`;
    arabian_sea `0, 25, 50, 78`; bay_of_bengal `0, 23, 78, 100`.
  - `class RegionFilter(Arguments)` with fields `region: Literal["equator", "arabian_sea",
    "bay_of_bengal"] | None`, `lat_min`, `lat_max` (`float | None`, −90…90), `lon_min`,
    `lon_max` (`float | None`, −180…180), `start_date`, `end_date` (`date | None`),
    `months: list[int] | None` (each 1–12, at most 12), and `wmo_id` (same pattern as
    `SearchArgs`). All use `allow_inf_nan=False`.
  - `RegionFilter.bounds() -> tuple[float, float, float, float] | None` returns
    `(lat_min, lat_max, lon_min, lon_max)` from the preset or the explicit fields.
  - `region_conditions(f: RegionFilter) -> tuple[list[str], list]` returns SQL condition
    strings and their parameters. Uses only the columns `latitude`, `longitude`, `date_utc`
    and `wmo_id`, which exist in both `profile_summary` and `observations`.
  - `describe_filter(f: RegionFilter) -> str` returns a short label for UI and chart titles.
  - `filter_dict(f: RegionFilter) -> dict` returns
    `f.model_dump(mode="json", exclude_none=True)` restricted to `RegionFilter` fields. It is
    JSON-safe and re-validates into the same filter.

- [ ] **Step 1: Write the failing tests** in `tests/test_regions.py`

```python
class RegionFilterTests(unittest.TestCase):
    def test_validation(self):
        for bad in [dict(lat_min=5, lat_max=-5), dict(lat_min=-5),            # inverted / unpaired
                    dict(region='equator', lat_min=-1, lat_max=1),            # preset + bounds
                    dict(months=[0]), dict(months=[13]), dict(region='pacific'),
                    dict(start_date='2024-06-30', end_date='2024-03-01'), dict(sql='SELECT 1')]:
            with self.assertRaises(ValueError):
                RegionFilter(**bad)
        self.assertEqual(RegionFilter(months=[6, 3, 3]).months, [3, 6])
        self.assertEqual(RegionFilter(region='equator').bounds(), (-5, 5, -180, 180))

    def test_region_conditions_are_parameterised(self):
        sql, params = region_conditions(RegionFilter(region='equator', months=[3, 4], wmo_id='2902115'))
        text = ' AND '.join(sql)
        self.assertIn('latitude BETWEEN %s AND %s', text)
        self.assertIn('EXTRACT(MONTH FROM date_utc)::int = ANY(%s)', text)
        self.assertIn([3, 4], params)
        self.assertIn(-5, params)
        self.assertNotIn('equator', text)
        self.assertNotIn('2902115', text)

    def test_region_conditions_wrap_antimeridian(self):
        sql, params = region_conditions(RegionFilter(lat_min=-5, lat_max=5, lon_min=170, lon_max=-170))
        self.assertIn('(longitude >= %s OR longitude <= %s)', sql)
        self.assertEqual(params[-2:], [170, -170])

    def test_end_date_is_inclusive(self):
        sql, params = region_conditions(RegionFilter(end_date='2024-06-30'))
        self.assertIn('date_utc < %s', sql)
        self.assertIn(date(2024, 7, 1), params)

    def test_describe_filter(self):
        self.assertEqual(describe_filter(RegionFilter(region='equator', months=[3, 4, 5, 6])), '5°S–5°N · Mar–Jun')
        self.assertEqual(describe_filter(RegionFilter(months=[3, 5])), 'Mar, May')
        self.assertEqual(describe_filter(RegionFilter(lat_min=0, lat_max=25, lon_min=50, lon_max=78,
                                                      start_date='2024-03-01', end_date='2024-06-30')),
                         '0°–25°N, 50°E–78°E · 2024-03-01 to 2024-06-30')
        self.assertEqual(describe_filter(RegionFilter(wmo_id='2902115')), 'WMO 2902115')
        self.assertEqual(describe_filter(RegionFilter()), 'All profiles')

    def test_filter_dict_round_trips(self):
        f = RegionFilter(region='equator', months=[3, 4], start_date='2024-01-01')
        self.assertEqual(RegionFilter(**json.loads(json.dumps(filter_dict(f)))), f)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p test_regions.py -v`
Expected: `ImportError: cannot import name 'RegionFilter'`.

- [ ] **Step 3: Implement** `REGIONS`, `RegionFilter`, `region_conditions`, `describe_filter`
  and `filter_dict` in `ocean_tools.py`

  - Validation goes in a `model_validator(mode="after")`, the same way as `SearchArgs`: bounds
    come in pairs, `lat_min ≤ lat_max`, no `region` together with explicit bounds, ordered
    dates, `end_date != date.max`. A field validator deduplicates and sorts `months`.
  - `region_conditions` always starts with the two range checks `search_profiles` uses,
    `latitude BETWEEN -90 AND 90` and `longitude BETWEEN -180 AND 180` (they exclude NULL
    positions). It then adds the latitude band, the longitude range
    (`BETWEEN` when `lon_min ≤ lon_max`, else the wrapped `OR` form), `date_utc >= %s`,
    `date_utc < %s` (end date + 1 day), the month condition and `wmo_id = %s`. Skip the
    longitude condition when the range is the full −180…180.
  - `describe_filter` joins non-empty parts with `" · "`. Latitudes are formatted `5°S`, `0°`
    and `5°N`, longitudes `50°E` / `170°W`. Bounds read `"{lat_min}–{lat_max}"`, followed by
    `", {lon_min}–{lon_max}"` when longitude isn't the full range. Consecutive months become
    a range (`Mar–Jun`), others a comma list (`Mar, May`). Dates read `"{start} to {end}"`,
    or `"from {start}"` / `"until {end}"`. Then `WMO {id}`. Empty filter → `"All profiles"`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -p test_regions.py -v`
Expected: 6 tests, `OK`.

- [ ] **Step 5: Commit**

```bash
git add ocean_tools.py tests/test_regions.py
git commit -m "Add RegionFilter with parameterised region, date and month conditions" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `region_profiles` tool and `region_points`

**Files:**
- Modify: `ocean_tools.py` (new functions, `TOOLS`, `ToolPlan`)
- Modify: `tests/test_etl.py` (`make_fixture` gains keyword arguments)
- Test: `tests/test_regions.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `RegionFilter`, `region_conditions`, `describe_filter`, `filter_dict` (Task 1).
- Produces:
  - `region_profiles(**filter_fields) -> dict` with keys `n_profiles: int`,
    `n_floats: int`, `first_date: str | None`, `last_date: str | None` (`YYYY-MM-DD`),
    `floats: list[{"wmo_id", "n_profiles", "first_date", "last_date"}]` (at most 100,
    most profiles first), `truncated: bool`, `filter: dict` (from `filter_dict`),
    `label: str` (from `describe_filter`), and
    `dashboard: "The map highlights these profiles."`.
  - `region_points(**filter_fields) -> {"rows": [{"profile_id", "wmo_id", "latitude",
    "longitude", "date_utc"}], "truncated": bool}`. Newest first, at most 5,000 rows,
    `truncated` when exactly 5,000. **Not** registered as a model tool; the dashboard uses it.
  - `TOOLS["region_profiles"] = (RegionFilter, region_profiles, "Count, list and highlight every profile inside a named region (equator, arabian_sea, bay_of_bengal) or a latitude/longitude box, optionally by dates, calendar months (1–12) or float.")`
  - `ToolPlan.tool`'s `Literal` gains `"region_profiles"`.
  - `make_fixture(path, temp=20, bad_pressure=False, *, latitude=12, longitude=179.9, juld=27000, wmo='2902115', cycle=1)`. The defaults keep every existing test unchanged.

- [ ] **Step 1: Write the failing tests**

In `tests/test_regions.py` (unit, no database):

```python
    def test_region_profiles_queries_summary_with_bound_parameters(self):
        with patch('ocean_tools.fetch_rows', side_effect=[
                [{'n_profiles': 2, 'n_floats': 1, 'first_date': datetime(2024, 3, 1), 'last_date': datetime(2024, 4, 1)}],
                [{'wmo_id': '2902115', 'n_profiles': 2, 'first_date': datetime(2024, 3, 1), 'last_date': datetime(2024, 4, 1)}]]) as fetch:
            result = execute_tool('region_profiles', {'region': 'equator', 'months': [3, 4]})
        self.assertTrue(all('profile_summary' in call.args[0] for call in fetch.call_args_list))
        self.assertEqual((result['n_profiles'], result['first_date'], result['label']), (2, '2024-03-01', '5°S–5°N · Mar–Apr'))
        self.assertEqual(result['filter'], {'region': 'equator', 'months': [3, 4]})
        json.dumps(result)  # must be JSON-safe for the dashboard store

    def test_region_points_caps_at_5000(self):
        with patch('ocean_tools.fetch_rows', return_value=[{'profile_id': i, 'date_utc': None} for i in range(5000)]) as fetch:
            self.assertTrue(region_points(region='equator')['truncated'])
        self.assertIn('LIMIT 5000', fetch.call_args.args[0])
```

In `tests/test_integration.py`, add a helper and two tests:

```python
def juld(day):  # days since 1950-01-01, as stored in Argo files
    return (datetime.fromisoformat(day) - datetime(1950, 1, 1)).days

EQUATOR_FIXTURES = [  # (cycle, lat, lon, date, temp)
    (1, 0.5, 80, '2024-03-15', 28),   # A: in band, March 2024
    (2, -1.0, 85, '2024-04-15', 26),  # B: in band, April 2024
    (3, 12.0, 72, '2024-03-20', 30),  # C: Arabian Sea, outside band
    (4, 1.0, 82, '2023-05-10', 27),   # D: in band, May of another year
    (5, 0.0, 80, '2024-08-01', 25),   # E: in band, outside March–June
]

def load_fixtures(conn, folder, fixtures=EQUATOR_FIXTURES, prefix='D'):
    for cycle, lat, lon, day, temp in fixtures:
        path = Path(folder) / f'{prefix}2902115_{cycle:03d}.nc'
        make_fixture(path, temp, latitude=lat, longitude=lon, juld=juld(day), cycle=cycle)
        assert etl.process_file(conn, path)['error'] is None
    assert etl.refresh_summary(conn)

    def test_region_profiles_equator_march_to_june(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = ocean_tools.region_profiles(region='equator', months=[3, 4, 5, 6])
        self.assertEqual((result['n_profiles'], result['n_floats']), (3, 1))
        self.assertEqual((result['first_date'], result['last_date']), ('2023-05-10', '2024-04-15'))
        self.assertEqual(len(ocean_tools.region_points(region='equator')['rows']), 4)

    def test_region_profiles_across_antimeridian(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder, [(1, 0, -179.5, '2024-03-01', 28), (2, 0, 100, '2024-03-01', 28)])
        result = ocean_tools.region_profiles(lat_min=-5, lat_max=5, lon_min=170, lon_max=-170)
        self.assertEqual(result['n_profiles'], 1)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p 'test_regions.py' -v` and
`python -m unittest discover -s tests -p 'test_integration.py' -k region -v`
Expected: failures for `region_profiles` / `region_points` not defined and an unexpected
`make_fixture` keyword.

- [ ] **Step 3: Implement**

  - `make_fixture`: write `wmo.ljust(8)` into `PLATFORM_NUMBER`, and `cycle`, `juld`,
    `latitude`, `longitude` into the scalar variables that currently hard-code them.
  - `region_profiles` validates `RegionFilter(**kwargs)` and runs two `fetch_rows` calls on
    `profile_summary` with `WHERE {' AND '.join(conditions)}`, totals first. The totals query selects
    `COUNT(*)`, `COUNT(DISTINCT wmo_id)`, `MIN/MAX(date_utc)`. The per-float query is
    `GROUP BY wmo_id ORDER BY n_profiles DESC, wmo_id LIMIT 101`. Convert dates to
    `YYYY-MM-DD` strings in Python.
  - `region_points` selects the five columns with
    `ORDER BY date_utc DESC NULLS LAST, profile_id DESC LIMIT 5000`, using
    `fetch_rows(..., max_rows=5000)`. Convert `date_utc` to `YYYY-MM-DD` strings.
  - Register the tool in `TOOLS` and add it to the `ToolPlan` `Literal`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: all tests `OK`, no skips.

- [ ] **Step 5: Commit**

```bash
git add ocean_tools.py tests/test_regions.py tests/test_integration.py tests/test_etl.py
git commit -m "Add region_profiles tool and region_points for map highlights" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `aggregate_measurements` tool

**Files:**
- Modify: `ocean_tools.py`
- Test: `tests/test_regions.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `RegionFilter`, `region_conditions`, `describe_filter`, `filter_dict` (Task 1);
  `load_fixtures`, `EQUATOR_FIXTURES` (Task 2).
- Produces:
  - `VARIABLES: dict[str, tuple[str, str, str]]` maps key → `(column, label, units)`:
    `temp` → `("temp", "Temperature", "°C")`, `psal` → `("psal", "Salinity", "PSU")`,
    `doxy` → `("doxy", "Oxygen", "µmol/kg")`, `chla` → `("chla", "Chlorophyll-a", "mg/m³")`,
    `nitrate` → `("nitrate", "Nitrate", "µmol/kg")`,
    `bbp700` → `("bbp700", "Backscatter 700 nm", "m⁻¹")`.
  - `class AggregateArgs(RegionFilter)` adds `variable: Literal["temp", "psal", "doxy", "chla", "nitrate", "bbp700"] = "temp"`,
    `group_by: Literal["week", "month", "month_of_year", "pressure"] = "month"`,
    `pres_min: float = 0` (0–6000), `pres_max: float | None = None` (above 0, at most 6000,
    must exceed `pres_min`), and `bin_dbar: int = 50` (10–500).
  - `aggregate_measurements(**args) -> dict` with keys `variable`, `label`, `units`,
    `group_by`, `pressure_range: [min, max]`, `bin_dbar`, `rows: list[{"bucket", "mean",
    "std", "min", "max", "n_profiles", "n_floats"}]`, `n_profiles`, `n_floats`,
    `first_date`, `last_date` (`YYYY-MM-DD` or `None`), `filter`, `filter_label`, and
    `dashboard: "The dashboard charts these values and highlights the profiles on the map."`.
    `bucket` is `'YYYY-MM-DD'` (week start) for `week`, `'YYYY-MM'` for `month`, an `int`
    1–12 for `month_of_year`, and a `float` (bin lower edge, dbar) for `pressure`.
  - Default `pres_max`: `10` for time groupings, `2000` for `pressure`.
  - `TOOLS["aggregate_measurements"] = (AggregateArgs, aggregate_measurements, "Average a variable (default temperature) over every profile matching a region, dates, calendar months or float. group_by week, month, month_of_year (seasonal, across years) or pressure (mean depth profile). Default layer 0–10 dbar. Use for averages, trends and graphs.")`, and `ToolPlan` gains `"aggregate_measurements"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_regions.py`:

```python
    def test_aggregate_sql_uses_whitelisted_column_and_bound_values(self):
        with patch('ocean_tools.fetch_rows', side_effect=[[], [{'n_profiles': 0, 'n_floats': 0, 'first_date': None, 'last_date': None}]]) as fetch:
            result = execute_tool('aggregate_measurements', {'region': 'equator', 'variable': 'doxy', 'group_by': 'month_of_year'})
        sql, params = fetch.call_args_list[0].args
        self.assertIn('AVG(doxy)', sql)
        self.assertIn('EXTRACT(MONTH FROM date_utc)', sql)
        self.assertEqual(params[:2], [0, 10])          # default surface layer
        self.assertEqual((result['rows'], result['units']), ([], 'µmol/kg'))

    def test_aggregate_rejects_unknown_variable_and_bad_layers(self):
        for bad in [{'variable': 'temp; DROP TABLE floats'}, {'group_by': 'year'},
                    {'pres_min': 50, 'pres_max': 10}, {'bin_dbar': 5}]:
            with self.assertRaises(ValueError):
                execute_tool('aggregate_measurements', bad)

    def test_aggregate_pressure_defaults_to_full_depth(self):
        with patch('ocean_tools.fetch_rows', side_effect=[[], [{'n_profiles': 0, 'n_floats': 0, 'first_date': None, 'last_date': None}]]):
            self.assertEqual(aggregate_measurements(group_by='pressure')['pressure_range'], [0, 2000])
```

In `tests/test_integration.py` (fixture levels kept by QC: 10 dbar = `temp`, 30 dbar =
`temp + 2`):

```python
    def test_aggregate_month_of_year_across_years(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = ocean_tools.aggregate_measurements(region='equator', months=[3, 4, 5, 6], group_by='month_of_year')
        self.assertEqual([(r['bucket'], r['mean'], r['n_profiles']) for r in result['rows']], [(3, 28, 1), (4, 26, 1), (5, 27, 1)])
        self.assertEqual((result['n_profiles'], result['first_date'], result['last_date']), (3, '2023-05-10', '2024-04-15'))

    def test_aggregate_month_buckets_for_one_year(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = ocean_tools.aggregate_measurements(region='equator', start_date='2024-03-01', end_date='2024-06-30')
        self.assertEqual([(r['bucket'], r['mean']) for r in result['rows']], [('2024-03', 28), ('2024-04', 26)])

    def test_aggregate_pressure_profile_uses_per_profile_means(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        rows = ocean_tools.aggregate_measurements(region='equator', months=[3, 4, 5, 6], group_by='pressure')['rows']
        self.assertEqual([(r['bucket'], r['mean'], r['n_profiles']) for r in rows], [(0, 28, 3)])   # (29 + 27 + 28) / 3

    def test_aggregate_counts_core_and_bgc_grids_once(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder, EQUATOR_FIXTURES[:1])
            load_fixtures(self.conn, folder, EQUATOR_FIXTURES[:1], prefix='BD')
        result = ocean_tools.aggregate_measurements(region='equator')
        self.assertEqual((result['n_profiles'], result['rows'][0]['n_profiles']), (1, 1))
```

Note: `load_fixtures` with `prefix='BD'` writes `BD2902115_001.nc`. The ETL gives it source key
`B2902115_001.nc`, so it's stored as a second grid of the same profile.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p 'test_*.py' -k aggregate -v`
Expected: `aggregate_measurements` / `AggregateArgs` not defined.

- [ ] **Step 3: Implement `aggregate_measurements`**

Average each profile first, then average across profiles. This is the core of D2.
`{column}` and `{bucket}` come only from `VARIABLES` and this fixed mapping:
`week` → `to_char(date_trunc('week', date_utc), 'YYYY-MM-DD')`,
`month` → `to_char(date_trunc('month', date_utc), 'YYYY-MM')`,
`month_of_year` → `EXTRACT(MONTH FROM date_utc)::int`,
`pressure` → `FLOOR(pres / %s) * %s`.

```sql
WITH per_profile AS (
    SELECT profile_id, wmo_id, {bucket} AS bucket, AVG({column}) AS value
    FROM observations
    WHERE {column} IS NOT NULL AND pres >= %s AND pres <= %s AND {region conditions}
    GROUP BY profile_id, wmo_id, 3
)
SELECT bucket, AVG(value) AS mean, STDDEV_SAMP(value) AS std, MIN(value) AS min, MAX(value) AS max,
       COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats
FROM per_profile GROUP BY bucket ORDER BY bucket
```

Parameter order: `[pres_min, pres_max, *region_params]`. For `pressure`, the two `bin_dbar`
values for the bucket expression come first. Use `fetch_rows(..., max_rows=1000)`, so too
many buckets raise `QueryLimitError` and the chat shows its generic "narrow the filters" reply.
A second query over the same `WHERE` returns `COUNT(DISTINCT profile_id)`,
`COUNT(DISTINCT wmo_id)`, `MIN(date_utc)` and `MAX(date_utc)` for the totals. Register the tool.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: all `OK`, no skips.

- [ ] **Step 5: Commit**

```bash
git add ocean_tools.py tests/test_regions.py tests/test_integration.py
git commit -m "Add aggregate_measurements: per-profile means by week, month, season or depth" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Chat returns dashboard actions

**Files:**
- Modify: `chat_service.py` (`answer_question`, both system prompts, new `dashboard_actions`)
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `ToolPlan`, the `region_profiles` and `aggregate_measurements` result shapes
  (Tasks 2–3).
- Produces:
  - `dashboard_actions(plan: ToolPlan, result: dict) -> dict | None`, which may return any of
    these keys:
    - `"select": {"profile_id", "wmo_id"}`: the current behaviour for `profile_data`,
      `search_profiles` and `trajectory`, moved here unchanged.
    - `"highlight": {"filter": result["filter"], "label": <label>}`: for `region_profiles`
      (label `result["label"]`) and `aggregate_measurements` (label
      `result["filter_label"]`), only when `result["n_profiles"] > 0`.
    - `"chart": result`: for `aggregate_measurements`, always, even when `rows` is empty.

    Returns `None` when there are no actions.
  - `answer_question(...) -> tuple[str, dict | None]`. The second value is now the actions
    dict, not a bare selection. `app.py` is updated in Task 5.

- [ ] **Step 1: Write the failing tests** in `tests/test_chat.py`

```python
    def test_dashboard_actions_for_each_tool(self):
        plan = lambda tool: chat.ToolPlan(tool=tool, arguments={})
        region = {'n_profiles': 3, 'filter': {'region': 'equator'}, 'label': '5°S–5°N'}
        aggregate = {'n_profiles': 3, 'filter': {'region': 'equator'}, 'filter_label': '5°S–5°N', 'rows': [{'bucket': 3}]}
        self.assertEqual(chat.dashboard_actions(plan('region_profiles'), region),
                         {'highlight': {'filter': {'region': 'equator'}, 'label': '5°S–5°N'}})
        actions = chat.dashboard_actions(plan('aggregate_measurements'), aggregate)
        self.assertEqual((actions['chart'], actions['highlight']['label']), (aggregate, '5°S–5°N'))
        self.assertEqual(chat.dashboard_actions(plan('trajectory'), {'rows': [{'profile_id': 7, 'wmo_id': '1'}]}),
                         {'select': {'profile_id': 7, 'wmo_id': '1'}})
        self.assertIsNone(chat.dashboard_actions(plan('dataset_summary'), {'floats': 2}))

    def test_actions_skip_empty_highlight(self):
        plan = chat.ToolPlan(tool='aggregate_measurements', arguments={})
        empty = {'n_profiles': 0, 'filter': {}, 'filter_label': 'All profiles', 'rows': []}
        self.assertEqual(chat.dashboard_actions(plan, empty), {'chart': empty})
        self.assertIsNone(chat.dashboard_actions(chat.ToolPlan(tool='region_profiles', arguments={}),
                                                 {'n_profiles': 0, 'filter': {}, 'label': 'x'}))

    def test_prompts_describe_regions_and_dashboard(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.generate', side_effect=['{"tool":"region_profiles","arguments":{"region":"equator"}}', 'Three.']) as model, \
             patch('chat_service.execute_tool', return_value={'n_profiles': 3, 'filter': {'region': 'equator'}, 'label': '5°S–5°N'}):
            answer, actions = chat.answer_question('Highlight floats on the equator')
        plan_prompt, answer_prompt = model.call_args_list[0].args[0], model.call_args_list[1].args[0]
        self.assertIn('March to June', plan_prompt)
        self.assertIn('5°S–5°N', plan_prompt)
        self.assertNotIn('claim a map was changed', answer_prompt)
        self.assertIn('dashboard', answer_prompt)
        self.assertEqual(actions['highlight']['label'], '5°S–5°N')
```

Update the existing `test_grounded_answer_and_selection`: rename `selection` to `actions` and
assert `actions['select']['profile_id'] == 1`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p test_chat.py -v`
Expected: `AttributeError: module 'chat_service' has no attribute 'dashboard_actions'`, and
the updated selection test fails.

- [ ] **Step 3: Implement**

  - Move the selection logic from the end of `answer_question` into `dashboard_actions`, and
    return `answer, dashboard_actions(plan, result)`.
  - Add to the **planning** prompt, in these words: *"Named areas: use region (equator =
    5°S–5°N, arabian_sea, bay_of_bengal) or explicit lat_min/lat_max/lon_min/lon_max. For
    months without a year, such as 'March to June', use months [3,4,5,6] and,
    for aggregates, group_by month_of_year. Use aggregate_measurements for averages, trends or graphs, and
    region_profiles to find, count or highlight all floats in an area."*
  - In the **answer** prompt, replace *"Never invent measurements, compute population
    statistics from a limited sample, or claim a map was changed."* with *"Never invent
    measurements or compute statistics yourself from a sample; aggregate results are
    already computed over every matching profile, so report them, naming the date span. Say
    the map or a chart changed only when the result's dashboard field says so."*

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -p test_chat.py -v`
Expected: 16 tests, `OK`. `test_app.py` may now fail where `handle_chat` reads the second
value; Task 5 fixes that. Check that nothing **else** regressed:
`python -m unittest discover -s tests -v`.

- [ ] **Step 5: Commit**

```bash
git add chat_service.py tests/test_chat.py
git commit -m "Return dashboard actions (select, highlight, chart) from chat answers" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Map highlight layer

**Files:**
- Modify: `app.py` (constants, `build_map`, `update_map_selection`, `load_data`,
  `handle_chat`, layout, new callbacks)
- Modify: `assets/style.css` (`--highlight` token, `.highlight-chip`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `region_points` (Task 2), the actions dict from `answer_question` (Task 4).
- Produces:
  - Constants `TRACE_PROFILES, TRACE_HIGHLIGHT, TRACE_TRAJECTORY, TRACE_HALO, TRACE_DOT = range(5)`
    and `HIGHLIGHT = "#fbbf24"`. The highlight sits above the grey dots and below the
    selection, so the selected profile is never hidden.
  - `highlight_trace(points: list[dict]) -> go.Scattermap`: amber markers, size 9, opacity
    0.9, `customdata=[[profile_id, wmo_id]]` so clicking them still selects a profile, hover
    `"WMO {id} · {date}<br>Highlighted"`.
  - `build_map(profiles, selected_wmo=None, trajectory=None, selected=None, highlight=None)`,
    where `highlight` is a list of point rows. Framing priority: trajectory, then highlight,
    then all profiles.
  - New stores `map-highlight` (`None | {"filter": dict, "label": str}`) and `analysis`
    (`None | chart dict`; rendered in Task 6).
  - Status-bar element `html.Div([html.Span(className="highlight-swatch"),
    html.Span(id="highlight-label"), html.Button("Clear", id="clear-highlight",
    className="btn btn-ghost btn-sm")], id="highlight-chip", className="highlight-chip",
    hidden=True)`.
  - `update_highlight(highlight) -> (Patch, label, hidden)`, triggered by `map-highlight`.
    It patches `data[TRACE_HIGHLIGHT]` and frames the map to the points when there are some.
    The label reads `"{label} · {n:,} profile(s)"` (singular for 1) plus `" (latest 5,000)"` when truncated.
  - `clear_highlight(n_clicks) -> None` sets `map-highlight` (with `allow_duplicate=True`).
  - `load_data(date_filter, selected_wmo=None, selected_profile=None, highlight=None)`,
    where the new `State("map-highlight", "data")` re-queries `region_points` so a Period
    change keeps the highlight.
  - `handle_chat` replaces its `chat-selection` output with three: `chat-selection`,
    `map-highlight` and `analysis`. It fills them from `actions["select"]`,
    `actions["highlight"]` and `actions["chart"]`, and sends `no_update` for missing keys,
    so a later answer without a highlight doesn't clear an existing one.

- [ ] **Step 1: Write the failing tests** in `tests/test_app.py`

```python
    POINT = dict(profile_id=9, wmo_id='2902115', latitude=0.5, longitude=80, date_utc='2024-03-15')

    def test_map_has_highlight_slot_under_selection(self):
        fig = app.build_map([], trajectory=[], selected=None, highlight=[self.POINT])
        self.assertEqual(len(fig.data), 5)
        self.assertEqual(list(fig.data[app.TRACE_HIGHLIGHT].lat), [0.5])
        self.assertEqual(list(fig.data[app.TRACE_HIGHLIGHT].customdata[0]), [9, '2902115'])
        self.assertEqual(fig.data[app.TRACE_DOT].name, 'Selected profile')

    def test_update_highlight_patches_its_slot(self):
        with patch('app.region_points', return_value={'rows': [self.POINT], 'truncated': False}) as points:
            patch_obj, label, hidden = app.update_highlight({'filter': {'region': 'equator'}, 'label': '5°S–5°N'})
        points.assert_called_once_with(region='equator')
        locations = [op['location'] for op in patch_obj.to_plotly_json()['operations']]
        self.assertIn(['data', app.TRACE_HIGHLIGHT], locations)
        self.assertEqual((label, hidden), ('5°S–5°N · 1 profile', False))
        _, _, hidden = app.update_highlight(None)
        self.assertTrue(hidden)

    def test_load_data_keeps_highlight(self):
        with patch('app.db_query', return_value=[]), \
             patch('app.region_points', return_value={'rows': [self.POINT], 'truncated': False}):
            _, fig, _, _ = app.load_data('30', None, None, {'filter': {'region': 'equator'}, 'label': 'x'})
        self.assertEqual(list(fig.data[app.TRACE_HIGHLIGHT].lat), [0.5])

    def test_selection_patch_leaves_highlight(self):
        with patch('app.get_trajectory', return_value=[]), patch('app.get_profile_meta', return_value=None):
            patch_obj = app.update_map_selection(None, None)
        locations = [op['location'] for op in patch_obj.to_plotly_json()['operations']]
        self.assertNotIn(['data', app.TRACE_HIGHLIGHT], locations)

    def test_handle_chat_routes_actions(self):
        chart = {'rows': []}
        highlight = {'filter': {'region': 'equator'}, 'label': 'x'}
        with app.server.test_request_context('/'), \
             patch('app.answer_question', return_value=('ok', {'highlight': highlight, 'chart': chart})):
            out = app.handle_chat(1, 0, 'equator', [], [])
        self.assertEqual(out[-3:], (no_update, highlight, chart))
```

Update the existing
`test_map_keeps_selection_trace_slots_and_frames_data` to expect 5 traces. Import `no_update`
from `dash` in the test module.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p test_app.py -v`
Expected: `AttributeError` for `TRACE_HIGHLIGHT` / `update_highlight`; the trace-count test
fails.

- [ ] **Step 3: Implement**

  - Replace the literal indices `1`, `2`, `3` in `update_map_selection` with the `TRACE_*`
    constants, and reorder its framing check to `if traj and "selected-wmo.data" in ctx.triggered_prop_ids`.
    Dash raises when `ctx` is read outside a callback, and the test calls the function directly.
  - `Patch.to_plotly_json()["operations"]` is Dash 3.4's patch format (`dash/_patch.py`). If
    the installed version differs, assert the same locations through its equivalent.
  - `update_highlight` catches database errors and returns `no_update` for all three outputs,
    the same as `update_map_selection`.
  - CSS: add `--highlight: #fbbf24;` to `:root`. `.highlight-chip` is an inline flex row
    inside `.statusbar`, with an 8 px round `.highlight-swatch` in `var(--highlight)`.
    `hidden` must still hide it (`.highlight-chip[hidden] { display: none; }`).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit**

```bash
git add app.py assets/style.css tests/test_app.py
git commit -m "Highlight a region's profiles on the map from chat answers" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Analysis panel and chart

**Files:**
- Modify: `app.py` (layout, `build_analysis_chart`, `render_analysis`, `close_analysis`)
- Modify: `assets/style.css` (`.center-bottom`, `.analysis`, narrow-screen order)
- Test: `tests/test_app.py`

Load the `dataviz` skill before writing the chart.

**Interfaces:**
- Consumes: the `analysis` store and the `aggregate_measurements` result shape (Task 3).
- Produces:
  - `build_analysis_chart(spec: dict) -> go.Figure`:
    - **Time groupings:** x = bucket labels (`month_of_year` → `"Mar"`, `"Apr"`…), y = mean,
      `lines+markers` in `COLORS[variable]` (`markers` only for a single bucket). Under it, a
      shaded band of mean ± std (two traces, the lower with `fill="tonexty"`, no hover). The
      y-axis title is `"{label} ({units}), {min}–{max} dbar"`. Leave the band out where `std` is
      `None` (a bucket with one profile). Hover shows the value,
      `n_profiles` and `n_floats`.
    - **`pressure`:** x = mean, y = bucket (dbar), y-axis reversed, the same band drawn
      horizontally.
    - **No rows:** `placeholder_figure("No measurements match this filter", height=160)`.
    - Uses `BASE_LAYOUT` and `AXIS`. Height 260.
  - Layout: in `col-center`, wrap the status bar and a new panel in
    `html.Div([...], className="center-bottom")`. The panel is
    `html.Section([html.Div([html.H3(id="analysis-title", className="chart-title"),
    html.Button("Close", id="close-analysis", className="btn btn-ghost btn-sm")],
    className="analysis-head"), dcc.Graph(id="analysis-chart",
    config={"displayModeBar": False}), html.P(id="analysis-caption", className="field-help")],
    id="analysis-panel", className="analysis glass", hidden=True)`.
  - `render_analysis(spec) -> (figure, title, caption, hidden)`.
    - Title: `"Mean {label lower-case} · {filter_label}"`, e.g.
      `"Mean temperature · 5°S–5°N · Mar–Jun"`.
    - Caption: `"Average of each profile's mean, {min}–{max} dbar · {n_profiles:,} profile(s) from {n_floats:,} float(s) · {first_date} to {last_date}"`, with correct singular and plural.
    - `None` → `(no_update, "", "", True)`.
  - `close_analysis(n_clicks) -> None` sets `analysis` (with `allow_duplicate=True`).

- [ ] **Step 1: Write the failing tests** in `tests/test_app.py`

```python
    SPEC = dict(variable='temp', label='Temperature', units='°C', group_by='month_of_year',
                pressure_range=[0, 10], bin_dbar=50, n_profiles=3, n_floats=1,
                first_date='2023-05-10', last_date='2024-04-15', filter_label='5°S–5°N · Mar–Jun',
                rows=[dict(bucket=3, mean=28, std=None, min=28, max=28, n_profiles=1, n_floats=1),
                      dict(bucket=4, mean=26, std=None, min=26, max=26, n_profiles=1, n_floats=1)])

    def test_analysis_chart_time_series(self):
        fig = app.build_analysis_chart(self.SPEC)
        line = [t for t in fig.data if t.name == 'Mean'][0]
        self.assertEqual((list(line.x), list(line.y)), (['Mar', 'Apr'], [28, 26]))
        self.assertIn('°C', fig.layout.yaxis.title.text)
        self.assertIn('0–10 dbar', fig.layout.yaxis.title.text)

    def test_analysis_chart_pressure_is_reversed(self):
        spec = dict(self.SPEC, group_by='pressure', pressure_range=[0, 2000],
                    rows=[dict(bucket=0, mean=28, std=1, min=27, max=29, n_profiles=3, n_floats=1)])
        fig = app.build_analysis_chart(spec)
        self.assertEqual(fig.layout.yaxis.autorange, 'reversed')

    def test_analysis_chart_empty(self):
        fig = app.build_analysis_chart(dict(self.SPEC, rows=[], n_profiles=0, n_floats=0))
        self.assertIn('No measurements match', fig.layout.annotations[0].text)

    def test_render_analysis(self):
        _, title, caption, hidden = app.render_analysis(self.SPEC)
        self.assertEqual(title, 'Mean temperature · 5°S–5°N · Mar–Jun')
        self.assertIn('3 profiles from 1 float', caption)
        self.assertIn('2023-05-10 to 2024-04-15', caption)
        self.assertFalse(hidden)
        self.assertTrue(app.render_analysis(None)[3])
        self.assertIsNone(app.close_analysis(1))
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p test_app.py -k analysis -v`
Expected: `AttributeError: module 'app' has no attribute 'build_analysis_chart'`.

- [ ] **Step 3: Implement** the chart, layout, the two callbacks and CSS

CSS:
  - `.center-bottom`: a flex column, gap 12 px, aligned left.
  - `.analysis`: `width: min(620px, 100%)`, `border-radius: var(--radius)`, padding 14 px,
    `pointer-events: auto`, with a `rise` reveal.
  - `.analysis[hidden] { display: none; }`.
  - In `@media (max-width: 1279px)`: `.center-bottom { display: contents; }`. Set the order to
    `.statusbar` 4, `.analysis` 5, `.sidebar` 6, `.inspector` 7, and `.analysis` full width.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit**

```bash
git add app.py assets/style.css tests/test_app.py
git commit -m "Add Analysis panel that charts aggregate results over the map" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Re-imports keep a profile's data mode current

**Files:**
- Modify: `argo_etl.py` (the `INSERT INTO profiles … ON CONFLICT` statement in `process_file`)
- Modify: `tests/test_etl.py` (`make_fixture` gains `data_mode='D'`)
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: `make_fixture` keywords from Task 2.
- Produces: `make_fixture(..., data_mode='D')`, which writes `DATA_MODE`. On conflict,
  `profiles.data_mode` and `profiles.source_file` take the incoming values when the incoming
  mode ranks at least as high as the stored one (`R` < `A` < `D`; NULL lowest). Otherwise they
  keep the stored ones. Latitude/longitude handling is unchanged.

- [ ] **Step 1: Write the failing test** in `tests/test_integration.py`

```python
    def test_reimport_upgrades_profile_data_mode_but_never_downgrades(self):
        with tempfile.TemporaryDirectory() as folder:
            real, delayed = Path(folder) / 'R2902115_001.nc', Path(folder) / 'D2902115_001.nc'
            make_fixture(real, data_mode='R')
            make_fixture(delayed)
            for path, expected in [(real, ('R', 'R2902115_001.nc')), (delayed, ('D', 'D2902115_001.nc')),
                                   (real, ('D', 'D2902115_001.nc'))]:
                self.assertIsNone(etl.process_file(self.conn, path)['error'])
                with self.conn.cursor() as cur:
                    cur.execute('SELECT data_mode, source_file FROM profiles')
                    self.assertEqual(cur.fetchone(), expected)
                self.conn.commit()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m unittest discover -s tests -p test_integration.py -k data_mode -v`
Expected: FAIL on the second import: `('R', 'R2902115_001.nc') != ('D', 'D2902115_001.nc')`.

- [ ] **Step 3: Implement.** In the `ON CONFLICT … DO UPDATE SET`, add `data_mode` and
  `source_file`, both chosen by
  `COALESCE(array_position(ARRAY['R','A','D'], EXCLUDED.data_mode::text), 0) >= COALESCE(array_position(ARRAY['R','A','D'], profiles.data_mode::text), 0)`.

- [ ] **Step 4: Run all tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit** — `"Keep a profile's data mode current when a better file is re-imported"`.

---

### Task 8: Top-bar totals use real counts

**Files:**
- Modify: `app.py` (`load_data`, new `get_totals`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `get_totals() -> dict` with `floats: int`, `profiles: int`, `latest` (a datetime or
  `None`), from `SELECT (SELECT COUNT(*) FROM floats) AS floats, COUNT(*) AS profiles,
  MAX(date_utc) AS latest FROM profiles`. Returns zeros and `None` when the query returns no
  rows. `load_data` builds its three KPIs from it and calls it **after** the profiles query,
  so `test_date_filter_is_parameterized_and_has_one_outer_where` still sees that query first.

- [ ] **Step 1: Write the failing test**

```python
    def test_kpis_use_true_totals_not_the_float_list(self):
        with patch('app.db_query', return_value=[]), \
             patch('app.get_totals', return_value={'floats': 1234, 'profiles': 98765, 'latest': datetime(2026, 10, 12)}):
            kpis = app.load_data('all')[3]
        self.assertIn('1,234', str(kpis))
        self.assertIn('98,765', str(kpis))
        self.assertIn('2026-10-12', str(kpis))
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m unittest discover -s tests -p test_app.py -k totals -v`
Expected: `AttributeError: … has no attribute 'get_totals'`.

- [ ] **Step 3: Implement** `get_totals` and use it in `load_data`'s KPIs.

- [ ] **Step 4: Run all tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit** — `"Show true float and profile totals in the top bar"`.

---

### Task 9: Regions tab, without the AI

**Files:**
- Modify: `app.py` (layout, tabs client-side callback, `region_highlight`, `region_chart`)
- Modify: `assets/style.css` (three-tab indicator)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `RegionFilter`, `describe_filter`, `filter_dict` (Task 1), `aggregate_measurements`
  (Task 3), `dashboard_actions` (Task 4), and the `map-highlight` / `analysis` stores (Tasks 5–6).
- Produces:
  - Tabs **Floats | Regions | Ask AI**. The new `html.Button([icon("pin"), "Regions"],
    id="tab-regions")` and `pane-regions` sit between the existing two. The client-side tab
    callback gets three inputs and sets `tabs` / `tabs is-regions` / `tabs is-chat`.
  - Pane fields, each a `.field` with a label:
    - `region-select`: Equator (5°S–5°N), Arabian Sea, Bay of Bengal. Default `equator`,
      not clearable.
    - `months-select`: Jan–Dec as `1`–`12`, `multi=True`, placeholder "All months".
    - `variable-select`: the `VARIABLES` labels. Default `temp`.
    - `group-select`: "By calendar month" `month_of_year` (default), "By month" `month`,
      "By week" `week`, "By depth" `pressure`.
    - Buttons `region-highlight-btn` ("Highlight", `btn btn-ghost`) and `region-chart-btn`
      ("Chart average", `btn btn-primary`), then `html.Div(id="region-feedback",
      className="field-help", role="status")`.
  - `region_highlight(n_clicks, region, months) -> (highlight | no_update, feedback: str)`
    writes `map-highlight` (with `allow_duplicate=True`). It returns
    `{"filter": filter_dict(f), "label": describe_filter(f)}` and `""`. On a `ValueError`
    it returns `no_update` and "Choose a region from the list.".
  - `region_chart(n_clicks, region, months, variable, group_by) -> (chart | no_update,
    highlight | no_update, feedback: str)` writes `analysis` and `map-highlight` (both
    `allow_duplicate=True`). It calls
    `aggregate_measurements(region=region, months=months or None, variable=variable, group_by=group_by)`,
    dropping `None` values, and routes the result through
    `dashboard_actions(ToolPlan(tool="aggregate_measurements", arguments={}), result)`.
    On `QueryLimitError` the feedback is "Too many points to chart. Choose fewer months or
    group by calendar month.". On any other error it is "Couldn't compute that average. Try
    again in a minute.". Exception text is never shown.

- [ ] **Step 1: Write the failing tests**

```python
    def test_region_highlight_builds_validated_filter(self):
        self.assertEqual(app.region_highlight(1, 'equator', [6, 3]),
                         ({'filter': {'region': 'equator', 'months': [3, 6]}, 'label': '5°S–5°N · Mar, Jun'}, ''))
        self.assertEqual(app.region_highlight(1, 'pacific', None), (no_update, 'Choose a region from the list.'))

    def test_region_chart_calls_aggregate_and_highlights(self):
        spec = {'n_profiles': 2, 'filter': {'region': 'equator'}, 'filter_label': '5°S–5°N', 'rows': []}
        with patch('app.aggregate_measurements', return_value=spec) as aggregate:
            chart, highlight, feedback = app.region_chart(1, 'equator', [3, 4, 5, 6], 'temp', 'month_of_year')
        aggregate.assert_called_once_with(region='equator', months=[3, 4, 5, 6], variable='temp', group_by='month_of_year')
        self.assertEqual((chart, highlight['label'], feedback), (spec, '5°S–5°N', ''))

    def test_region_chart_reports_errors_without_details(self):
        with patch('app.aggregate_measurements', side_effect=QueryLimitError('Result exceeds 1000 rows')):
            self.assertEqual(app.region_chart(1, 'equator', [], 'temp', 'week')[:2], (no_update, no_update))
        with patch('app.aggregate_measurements', side_effect=RuntimeError('password=secret')):
            feedback = app.region_chart(1, 'equator', [], 'temp', 'week')[2]
        self.assertNotIn('secret', feedback)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest discover -s tests -p test_app.py -k region_ -v`
Expected: `AttributeError: … has no attribute 'region_highlight'`.

- [ ] **Step 3: Implement** the pane, both callbacks and the tab changes. CSS: `.tabs`
  becomes `grid-template-columns: repeat(3, 1fr)`, the indicator width
  `calc((100% - 8px) / 3)`, `.tabs.is-regions::before { transform: translateX(100%); }` and
  `.tabs.is-chat::before { transform: translateX(200%); }`. A `.region-actions` row holds
  the two buttons side by side.

- [ ] **Step 4: Run all tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit** — `"Add a Regions tab that highlights and charts without the AI"`.

---

### Task 10: The new tools in the MCP server

**Files:**
- Modify: `postgres_mcp.py`
- Test: `tests/test_mcp.py` (new)

**Interfaces:**
- Consumes: `execute_tool` names `region_profiles` and `aggregate_measurements`.
- Produces:
  - `find_region_profiles(region=None, lat_min=None, lat_max=None, lon_min=None,
    lon_max=None, start_date=None, end_date=None, months=None, wmo_id=None) -> str`
  - `aggregate_measurements(variable="temp", group_by="month", pres_min=0, pres_max=None,
    bin_dbar=50, region=None, …same region fields…) -> str`

  Both are `@mcp.tool()` functions that forward only non-`None` arguments to `execute_tool`
  and return `json.dumps(..., default=str)`.

- [ ] **Step 1: Write the failing test**

```python
class McpTests(unittest.TestCase):
    def test_region_tools_forward_only_given_arguments(self):
        with patch('postgres_mcp.execute_tool', return_value={'n_profiles': 1}) as run:
            self.assertEqual(json.loads(postgres_mcp.find_region_profiles(region='equator', months=[3])), {'n_profiles': 1})
            run.assert_called_with('region_profiles', {'region': 'equator', 'months': [3]})
            postgres_mcp.aggregate_measurements(region='equator')
            name, arguments = run.call_args.args
        self.assertEqual((name, arguments['group_by'], arguments['region']), ('aggregate_measurements', 'month', 'equator'))
        self.assertNotIn('pres_max', arguments)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m unittest discover -s tests -p test_mcp.py -v`
Expected: `AttributeError: module 'postgres_mcp' has no attribute 'find_region_profiles'`.

- [ ] **Step 3: Implement** both tools.

- [ ] **Step 4: Run all tests and confirm they pass**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

- [ ] **Step 5: Commit** — `"Expose region and aggregate tools over MCP"`.

---

### Task 11: Remove the unused dependency and rewrite the README

**Files:**
- Modify: `requirements.txt`, `requirements.lock` (delete the `dash-bootstrap-components` lines)
- Modify: `README.md` (full rewrite)

- [ ] **Step 1: Confirm nothing uses it**

Run: `grep -rn "dash_bootstrap\|dbc\." --include='*.py' .`
Expected: no output.

- [ ] **Step 2: Remove it, uninstall it, and prove the app still works**

```bash
pip uninstall -y dash-bootstrap-components
pip check
python -m unittest discover -s tests -v
```

Expected: `No broken requirements found.`, and every test `OK`, no skips.

- [ ] **Step 3: Rewrite `README.md`** from `docs/project-guide.md`. Cover what FloatChat is,
  the features (including regions and the Analysis chart), a quick start (Docker, then native
  Python), loading data, turning on the AI (visitor OpenRouter key, server OpenRouter,
  Bedrock, Ollama), example questions including the two equator ones, the repository layout,
  and links to the guide and the AWS doc. Keep the existing license and acknowledgment lines
  (but drop the OpenAI one).

- [ ] **Step 4: Check that the stale content is gone**

Run: `grep -nE "GPT-4o|LangChain|LangGraph|OPENAI_API_KEY|SYSTEM_PROMPT|nest_asyncio|OCEAN_DARK|mapbox" README.md`
Expected: no output.

- [ ] **Step 5: Commit** — `"Drop unused dash-bootstrap-components; rewrite the README for the current app"`.

---

### Task 12: End-to-end check with real data, and docs

**Files:**
- Modify: `app.py` (`SUGGESTIONS`)
- Modify: `docs/project-guide.md` (sections 10, 11, 17, 18)

- [ ] **Step 1: Add the suggestion chip.** Append `"Average temperature on the equator, Mar–Jun"`
  to `SUGGESTIONS`.

- [ ] **Step 2: Start the local stack.** Ask the user first, because this creates a local
  `.env` with generated passwords and a ~200 MB download.

```bash
cp env.example .env   # then set two different random values for POSTGRES_PASSWORD and APP_DB_PASSWORD
docker compose up --build -d --wait
python download_argo.py --type Sprof --out-dir data/argo --wmo 2902272 7901136 5907092 2902114 2902158 2902296 1902751 1902594 6990503 5906970 6990505 6904118 1902458 2903831 5906539 1902455 2903465 5906500 5906537 5906501
docker compose run --rm etl --dir /data/argo --pattern '*_Sprof.nc'
```

Expected: the import ends with `Refreshed profile_summary`. These are the 20 Indian Ocean BGC
floats from `docs/aws-deployment.md`, some of them equatorial.

- [ ] **Step 3: Check the tools directly, with timing**

```bash
docker compose exec -T app python -c "import time, json; from ocean_tools import execute_tool as t; s=time.time(); r=t('aggregate_measurements', {'region':'equator','months':[3,4,5,6],'group_by':'month_of_year'}); print(round(time.time()-s,2), 's'); print(json.dumps(r, default=str)[:800])"
docker compose exec -T app python -c "from ocean_tools import execute_tool as t; r=t('region_profiles', {'region':'equator'}); print(r['n_profiles'], r['n_floats'], r['label'])"
```

Expected: both return non-empty results, and the aggregate takes **well under 10 s**. If it
comes close, run the same `WHERE` under `EXPLAIN ANALYZE` in `docker compose exec db psql`
and report the plan before adding any index.

- [ ] **Step 4: Check it in the browser** at <http://localhost:8080> (built-in browser pane).
  The **user** pastes their own OpenRouter key into AI settings; Claude must not type it.
  Then ask:
  1. *"Highlight all the floats on the equator"*: amber dots appear, the map frames them, the
     status bar shows `5°S–5°N · N profiles` with Clear, and the answer names floats and dates.
  2. *"Graph the average temperature of floats on the equator from March to June"*: the
     Analysis panel shows Mar–Jun points with a band, the caption gives counts and the date
     span, the highlight updates, and the answer names the years covered.
  3. Change Period to "Last 90 days of data": the highlight stays. Click an amber dot: the
     inspector opens that profile. Click Clear and Close: both disappear.
  4. Resize to phone width (`resize_window` preset `mobile`): the panel stacks under the
     status bar with no horizontal scroll. Then reset to `desktop`.
  5. With **no** key saved, open the **Regions** tab, pick Equator and Mar–Jun, and click Chart
     average. The same chart and highlight appear without the AI. Pick "By week" with all
     months: either a chart or the "Too many points" message, never an error page.
  6. The top-bar totals match
     `docker compose exec -T db psql -U floatchat_admin -d floatchat -c "SELECT (SELECT COUNT(*) FROM floats), COUNT(*) FROM profiles"`.

  Report any answer where the model picked the wrong tool (the answer will say the operation
  can't answer the question). That points to prompt wording, not code.

- [ ] **Step 5: Update `docs/project-guide.md`**: add the two tools to the tool table in
  section 11 and the new stores, callbacks, Regions tab and panel to section 10. Remove the
  "claim a map was changed" wording. Strike the fixed gotchas from section 17 (README,
  data mode, KPI cap, unused dependency). Add rows to section 18 ("Add a named region":
  `REGIONS`). Add the two MCP tools to section 12.

- [ ] **Step 6: Run the full suite and commit**

Run: `python -m unittest discover -s tests -v`
Expected: everything `OK`, no skips.

```bash
git add app.py docs/project-guide.md docs/execution-plan.md
git commit -m "Document region analysis and add an equator suggestion" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
docker stop floatchat-test-db
```

---

## Not in this plan

- Letting one question run two tools. Not needed, because `aggregate_measurements` already
  highlights its profiles.
- Pushing the branch, opening a PR or deploying to the server. Ask first.
