# Natural-Language Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any English data question becomes a validated analysis request (measure, statistic, grouping, comparison, filters, chart type) that the server runs safely, charts, and explains, or answers honestly that the data can't support it.

**Architecture:** A new `analysis.py` engine compiles an `AnalysisRequest` into parameterised SQL from fixed fragments and returns a result with a `status` (`ok`, `no_data`, `low_data`, `too_many`) and a diagnosis. `charts.py` draws any result. The `analyze` tool replaces `aggregate_measurements` in the chat, the Analyze tab and the MCP server. Region code moves to `regions.py`, design tokens to `theme.py`.

**Tech Stack:** Python 3.12, Pydantic 2, psycopg2, PostgreSQL 16, Dash 3.4 / Plotly 6.9, numpy, `unittest`.

**Spec:** [docs/superpowers/specs/2026-10-18-natural-language-charts-design.md](../specs/2026-10-18-natural-language-charts-design.md)

## Global Constraints

- No new dependencies; install only from `requirements.lock`.
- The model never writes SQL. Only fixed fragments, chosen by validated `Literal` values, are interpolated into SQL; every other value is a `%s` parameter.
- All reads go through `database.fetch_rows` (read-only role, statement timeout, row caps).
- At most **three** model calls per chat question (plan, one repair, answer).
- At most 1,000 points per series; histogram, scatter and map at most 5,000 rows.
- A point built from fewer than **3** profiles is "low confidence".
- Default depth layer 0–10 dbar; 0–2,000 for `profile` charts; 0–6,000 for `scatter`. A given `pres_min` without `pres_max` uses `pres_min + 10` (or `max(2000, pres_min + depth_bin)` for profiles).
- Everything stored in a `dcc.Store` is JSON-serialisable (dates as `YYYY-MM-DD` strings).
- Multi-series colours, in order: `#3987e5`, `#d95926`, `#199e70`, `#c98500` (dataviz reference dark slots 1–4); sequential map scale `[[0, "#b7d3f6"], [0.5, "#5598e7"], [1, "#1c5cab"]]`.
- `unittest`, run from the repository root. Branch `feature/region-analysis`. One commit per task; messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Pacific Ocean box crosses ±180°** → the wrapped longitude condition is used; with Indian Ocean data the result is `no_data` with "No profiles in the Pacific Ocean (approximate box) at all." *(Task 1 `test_ocean_regions`; Task 5 `test_pacific_has_no_data_and_says_why`)*
2. **A very broad request** (daily points over all years) → `status: too_many` with the narrowing advice, never a traceback. *(Task 3 `test_too_many_points_becomes_a_status`)*
3. **A comparison where one series has a single profile** → that series' points are `low: true`, drawn faded, and the caption says so. *(Task 3 `test_compare_regions_marks_low_confidence`; Task 6 `test_low_points_are_faded`)*
4. **The model returns an invalid plan twice** → a friendly "couldn't turn that into a supported analysis" answer after exactly two model calls. *(Task 9 `test_second_invalid_plan_gives_up_politely`)*
5. **The measurement exists only deeper than the layer** → "…has oxygen measurements, but none between 0 and 10 dbar." *(Task 5 `test_measured_only_deeper`)*

---

## Before you start

- [ ] **Environment.** `.venv` exists (`.venv/bin/pip install -r requirements.lock` if not).
- [ ] **Test database.** Point `TEST_DATABASE_URL` at a disposable database whose name ends in `_test`. On this machine, if the scratchpad cluster isn't running:

```bash
SP=/tmp/claude-1000/-home-saad-nathani-Documents-FloatChat/ed7f910d-46b6-4567-936c-c352ef1d49ec/scratchpad
/usr/lib/postgresql/16/bin/pg_ctl -D $SP/pg/data -l $SP/pg/server.log -o "-p 55432 -c unix_socket_directories='' -c listen_addresses=127.0.0.1" start
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/floatchat_test
```

- [ ] **Baseline.** Run: `.venv/bin/python -m unittest discover -s tests` → Expected: `Ran 76 tests` … `OK`.

---

### Task 1: `regions.py` with ocean regions

**Files:**
- Create: `regions.py`
- Modify: `ocean_tools.py` (move region code out, re-export it)
- Test: `tests/test_regions.py`

**Interfaces:**
- Produces in `regions.py`: `Arguments` (moved), `MONTH_NAMES`, `REGIONS`, `RegionName`, `RegionFilter`, `region_conditions`, `describe_filter`, `filter_dict` (all moved unchanged except as below) and new `region_phrase(f: RegionFilter) -> str`.
- `REGIONS[key]` = `{"lat_min", "lat_max", "lon_min", "lon_max", "label", "phrase", "approximate"}`:

| key | lat | lon | label | phrase | approximate |
| --- | --- | --- | --- | --- | --- |
| `equator` | −5, 5 | −180, 180 | Equator | the equatorial band (5°S–5°N) | False |
| `arabian_sea` | 0, 25 | 50, 78 | Arabian Sea | the Arabian Sea | False |
| `bay_of_bengal` | 0, 23 | 78, 100 | Bay of Bengal | the Bay of Bengal | False |
| `indian_ocean` | −60, 30 | 20, 120 | Indian Ocean | the Indian Ocean (approximate box) | True |
| `pacific_ocean` | −60, 60 | 120, −70 | Pacific Ocean | the Pacific Ocean (approximate box) | True |
| `atlantic_ocean` | −60, 65 | −70, 20 | Atlantic Ocean | the Atlantic Ocean (approximate box) | True |
| `southern_ocean` | −90, −60 | −180, 180 | Southern Ocean | the Southern Ocean (approximate box) | True |
| `arctic_ocean` | 66, 90 | −180, 180 | Arctic Ocean | the Arctic Ocean (approximate box) | True |

- `RegionName = Literal[...]` listing those eight keys; `RegionFilter.region: RegionName | None`.
- `describe_filter`: an approximate region contributes `"{label} (approx.)"` instead of its bounds; everything else is unchanged.
- `region_phrase`: named region → its `phrase`; explicit box → `"the box " + <area part of describe_filter>`; no area → `"the database"`.
- `ocean_tools.py` keeps working imports: `from regions import Arguments, MONTH_NAMES, REGIONS, RegionFilter, describe_filter, filter_dict, region_conditions`.

- [ ] **Step 1: Write the failing tests** (append to `RegionFilterTests`)

```python
    def test_ocean_regions(self):
        from regions import REGIONS, RegionName, region_phrase
        self.assertEqual(set(get_args(RegionName)), set(REGIONS))
        sql, params = region_conditions(RegionFilter(region='pacific_ocean'))
        self.assertIn('(longitude >= %s OR longitude <= %s)', sql)
        self.assertEqual(params[-2:], [120, -70])
        self.assertEqual(describe_filter(RegionFilter(region='pacific_ocean', months=[3])), 'Pacific Ocean (approx.) · Mar')
        self.assertEqual(region_phrase(RegionFilter(region='pacific_ocean')), 'the Pacific Ocean (approximate box)')
        self.assertEqual(region_phrase(RegionFilter(lat_min=0, lat_max=10, lon_min=60, lon_max=70)), 'the box 0°–10°N, 60°E–70°E')
        self.assertEqual(region_phrase(RegionFilter()), 'the database')
```

(add `from typing import get_args` to the imports)

- [ ] **Step 2: Run it and confirm it fails** — `.venv/bin/python -m unittest discover -s tests -p test_regions.py -k ocean` → Expected: `ModuleNotFoundError: No module named 'regions'`.
- [ ] **Step 3: Implement** — move the code, extend `REGIONS`, add `region_phrase`, re-export from `ocean_tools`.
- [ ] **Step 4: Run the whole suite** — `.venv/bin/python -m unittest discover -s tests` → Expected: `OK`, 77 tests.
- [ ] **Step 5: Commit** — `"Move region filters to regions.py and add approximate ocean regions"`.

---

### Task 2: `AnalysisRequest`

**Files:**
- Create: `analysis.py`
- Test: `tests/test_analysis.py` (new)

**Interfaces:**
- Consumes: `RegionFilter`, `REGIONS` (Task 1).
- Produces in `analysis.py`:
  - `MEASURES = {"temp": ("temp", "Temperature", "°C"), "psal": ("psal", "Salinity", "PSU"), "doxy": ("doxy", "Oxygen", "µmol/kg"), "chla": ("chla", "Chlorophyll-a", "mg/m³"), "nitrate": ("nitrate", "Nitrate", "µmol/kg"), "bbp700": ("bbp700", "Backscatter 700 nm", "m⁻¹"), "profiles": (None, "Profiles", "profiles")}`
  - `STAT_LABELS = {"mean": "Mean", "median": "Median", "min": "Minimum", "max": "Maximum", "std": "Standard deviation of", "count": "Number of", "p10": "10th percentile of", "p90": "90th percentile of"}`
  - `GROUP_LABELS = {"none": "", "day": "day", "week": "week", "month": "month", "year": "year", "month_of_year": "calendar month", "depth": "depth", "float": "float", "lat_band": "latitude band", "lon_band": "longitude band"}`
  - `TIME_GROUPS = ("day", "week", "month", "year", "month_of_year")`, `LOW_PROFILES = 3`, `MAX_POINTS = 1000`
  - `class AnalysisRequest(RegionFilter)` with fields `measure` (MEASURES keys, default `"temp"`), `stat` (STAT_LABELS keys, default `"mean"`), `group_by` (GROUP_LABELS keys, default `"month"`), `compare_by` (`"none"`, `"region"`, `"float"`, `"year"`; default `"none"`), `compare_values: list[str | int]` (max 4, default empty), `x_measure` (MEASURES keys except `profiles`, or `None`), `chart` (`"auto"`, `"line"`, `"bar"`, `"scatter"`, `"histogram"`, `"profile"`, `"map"`; default `"auto"`), `pres_min: float | None` (0–6000), `pres_max: float | None` (>0, ≤6000), `depth_bin: int = 50` (10–500), `band_deg: float = 5` (1–20), `bins: int = 30` (5–60).
  - Methods: `effective_group_by() -> str` (`"depth"` for `profile`; `"none"` for `scatter`/`histogram`/`map`; else `group_by`), `resolved_chart() -> str` (explicit chart, else depth→`profile`, time group→`line`, otherwise `bar`), `layer() -> tuple[float, float]` (Global Constraints defaults).
- Validation rules, each a `ValueError` with exactly this message:

| Condition | Message |
| --- | --- |
| `measure == "profiles"` and `stat != "count"` | `Profile counts use stat count.` |
| `measure == "profiles"` and chart in scatter/histogram/profile/map, or group depth | `Profile counts can't be drawn as that chart.` |
| `resolved_chart() == "scatter"` and (no `x_measure`, or `x_measure == measure`) | `A scatter chart needs a different x_measure.` |
| `x_measure` set and chart isn't scatter | `x_measure is only used by scatter charts.` |
| `compare_by != "none"` and not 1–4 `compare_values`, or `compare_by == "none"` with values | `compare_values must list 1–4 values for compare_by.` |
| region compare value not in `REGIONS` / float value not 5–8 digits / year not 1990–2100 | `Unknown comparison value.` |
| `compare_by == "region"` with `region` or bounds set | `Put the regions in compare_values, not region.` |
| `compare_by == "year"` and effective group not `month_of_year`/`none` | `Comparing years needs group_by month_of_year or none.` |
| `compare_by != "none"` and chart in scatter/histogram/map | `Comparisons work with line, bar and profile charts.` |
| chart `line` and effective group not a time group | `Line charts need a time grouping.` |
| chart `bar` and effective group `depth` | `Depth grouping is drawn as a profile chart.` |
| `pres_max` set and `<= layer start` | `pres_max must exceed pres_min.` |

Year values are normalised to `int`, WMO values to `str`.

- [ ] **Step 1: Write the failing tests** in `tests/test_analysis.py`

```python
class AnalysisRequestTests(unittest.TestCase):
    def test_rules_reject_impossible_combinations(self):
        cases = {
            'Profile counts use stat count.': dict(measure='profiles', stat='mean'),
            "Profile counts can't be drawn as that chart.": dict(measure='profiles', stat='count', chart='map'),
            'A scatter chart needs a different x_measure.': dict(chart='scatter'),
            'x_measure is only used by scatter charts.': dict(x_measure='psal'),
            'compare_values must list 1–4 values for compare_by.': dict(compare_by='region'),
            'Unknown comparison value.': dict(compare_by='region', compare_values=['atlantis']),
            'Put the regions in compare_values, not region.': dict(compare_by='region', compare_values=['equator'], region='arabian_sea'),
            'Comparing years needs group_by month_of_year or none.': dict(compare_by='year', compare_values=[2024], group_by='month'),
            'Comparisons work with line, bar and profile charts.': dict(compare_by='float', compare_values=['2902115'], chart='histogram'),
            'Line charts need a time grouping.': dict(chart='line', group_by='float'),
            'Depth grouping is drawn as a profile chart.': dict(chart='bar', group_by='depth'),
            'pres_max must exceed pres_min.': dict(pres_min=100, pres_max=50),
        }
        for message, fields in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, re.escape(message)):
                AnalysisRequest(**fields)

    def test_chart_and_layer_resolution(self):
        self.assertEqual(AnalysisRequest(group_by='depth').resolved_chart(), 'profile')
        self.assertEqual(AnalysisRequest(group_by='month_of_year').resolved_chart(), 'line')
        self.assertEqual(AnalysisRequest(group_by='lat_band').resolved_chart(), 'bar')
        self.assertEqual(AnalysisRequest(chart='profile', group_by='month').effective_group_by(), 'depth')
        self.assertEqual(AnalysisRequest().layer(), (0, 10))
        self.assertEqual(AnalysisRequest(chart='profile').layer(), (0, 2000))
        self.assertEqual(AnalysisRequest(chart='scatter', x_measure='psal').layer(), (0, 6000))
        self.assertEqual(AnalysisRequest(pres_min=100).layer(), (100, 110))
        self.assertEqual(AnalysisRequest(compare_by='year', compare_values=['2024'], group_by='month_of_year').compare_values, [2024])
```

- [ ] **Step 2: Run them and confirm they fail** — `-p test_analysis.py` → Expected: `ModuleNotFoundError: No module named 'analysis'`.
- [ ] **Step 3: Implement** `AnalysisRequest` (rules in one `model_validator(mode="after")`, in table order).
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Add AnalysisRequest with combination rules"`.

---

### Task 3: Grouped analyses (line, bar, profile, comparisons)

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_analysis.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: Task 2; `region_conditions`, `filter_dict`, `describe_filter` (Task 1); `fetch_rows`, `QueryLimitError`.
- Produces: `run_analysis(**request) -> dict` with the spec's result shape (§2 "Result shape"), plus top-level `n_profiles` (sum over series), `filter_label`, `depth_bin`, `x_measure`, `bins`, and `filter` (`filter_dict` of the base filter; `None` when `compare_by` is `region` or `float`). In this task `diagnosis` and `coverage` are `None` (Task 5 fills them for `no_data`).
- Fixed fragments:
  - group: `none` → `'All'`; `day` → `to_char(date_utc, 'YYYY-MM-DD')`; `week` → `to_char(date_trunc('week', date_utc), 'YYYY-MM-DD')`; `month` → `to_char(date_trunc('month', date_utc), 'YYYY-MM')`; `year` → `EXTRACT(YEAR FROM date_utc)::int`; `month_of_year` → `EXTRACT(MONTH FROM date_utc)::int`; `depth` → `FLOOR(pres / %s) * %s`; `float` → `wmo_id`; `lat_band` → `FLOOR(latitude / %s) * %s`; `lon_band` → `FLOOR(longitude / %s) * %s`.
  - stat: `mean` → `AVG(v)`; `median` → `percentile_cont(0.5) WITHIN GROUP (ORDER BY v)`; `std` → `STDDEV_SAMP(v)`; `p10`/`p90` → `percentile_cont(0.1|0.9) WITHIN GROUP (ORDER BY v)`; `min` → `MIN(lo)`; `max` → `MAX(hi)`; `count` → `COUNT(*)`.
- Algorithm (per series):

```sql
WITH per_profile AS (
    SELECT profile_id, wmo_id, <group> AS g, AVG(<col>) AS v, MIN(<col>) AS lo, MAX(<col>) AS hi
    FROM observations
    WHERE <col> IS NOT NULL AND pres >= %s AND pres <= %s AND <series conditions>
    GROUP BY profile_id, wmo_id, 3)
SELECT g AS x, <stat> AS y, COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats
FROM per_profile GROUP BY g ORDER BY g
```

  Parameter order: group parameters (if any), layer, series conditions. `measure="profiles"` counts from `profile_summary` instead: `SELECT <group> AS x, COUNT(*) AS y, COUNT(*) AS n_profiles, COUNT(DISTINCT wmo_id) AS n_floats FROM profile_summary WHERE <conditions> GROUP BY 1 ORDER BY 1` (never `low`). A per-series totals query returns `n_profiles`, `n_floats`, `first_date`, `last_date` with the same `WHERE`.
  Series: no compare → one series named `describe_filter(base)`; `region` → base with `region=v`, named `REGIONS[v]["label"]`; `float` → base with `wmo_id=v`, named `"WMO {v}"`; `year` → base plus `EXTRACT(YEAR FROM date_utc)::int = %s`, named `str(v)`.
  `fetch_rows(..., max_rows=MAX_POINTS)`; a `QueryLimitError` anywhere → `status: "too_many"`, `series: []`.
  Points: `{"x", "y", "n_profiles", "n_floats", "low": n_profiles < LOW_PROFILES}` (`x` as returned; `depth`/band edges as floats). Status: no points in any series → `no_data`; every point low → `low_data`; else `ok`.
  Title: `"{STAT_LABELS[stat]} {label.lower()}"` + `" by {GROUP_LABELS[g]}"` when `g != "none"` + `" · "` + (`" vs ".join(series names)` when comparing, else `describe_filter(base)`).
  Notes: `"Region boxes are approximate."` when any used region is approximate; `"Points from fewer than 3 profiles are faded (low confidence)."` when any point is low.

- [ ] **Step 1: Write the failing tests**

Unit (`tests/test_analysis.py`, `fetch_rows` patched):

```python
    def test_sql_is_fixed_fragments_with_bound_values(self):
        with patch('analysis.fetch_rows', side_effect=[[{'x': 3, 'y': 28.0, 'n_profiles': 4, 'n_floats': 1}],
                                                       [{'n_profiles': 4, 'n_floats': 1, 'first_date': None, 'last_date': None}]]) as fetch:
            result = run_analysis(measure='doxy', stat='median', group_by='lat_band', band_deg=2.5,
                                  region='equator', months=[3, 4], wmo_id='2902115')
        sql, params = fetch.call_args_list[0].args
        self.assertIn('percentile_cont(0.5) WITHIN GROUP (ORDER BY v)', sql)
        self.assertIn('FLOOR(latitude / %s) * %s', sql)
        for value in ('2902115', '2.5', 'equator'):
            self.assertNotIn(value, sql)
        self.assertEqual(params[:4], [2.5, 2.5, 0, 10])
        self.assertEqual((result['status'], result['chart'], result['title']),
                         ('ok', 'bar', 'Median oxygen by latitude band · 5°S–5°N · Mar–Apr · WMO 2902115'))

    def test_too_many_points_becomes_a_status(self):
        with patch('analysis.fetch_rows', side_effect=QueryLimitError('Result exceeds 1000 rows')):
            result = run_analysis(group_by='day')
        self.assertEqual((result['status'], result['series']), ('too_many', []))
```

Integration (`tests/test_integration.py`, using `load_fixtures` / `EQUATOR_FIXTURES`; levels kept by the fixture's QC: 10 dbar = `temp`, 30 dbar = `temp + 2`):

```python
    def test_analysis_mean_by_calendar_month_across_years(self):        # replaces the aggregate test
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = analysis.run_analysis(region='equator', months=[3, 4, 5, 6], group_by='month_of_year')
        points = result['series'][0]['points']
        self.assertEqual([(p['x'], p['y'], p['n_profiles']) for p in points], [(3, 28, 1), (4, 26, 1), (5, 27, 1)])
        self.assertEqual((result['series'][0]['first_date'], result['series'][0]['last_date']), ('2023-05-10', '2024-04-15'))

    def test_max_is_the_raw_maximum_and_median_uses_profile_means(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        top = analysis.run_analysis(region='equator', stat='max', group_by='none', pres_max=2000)
        self.assertEqual(top['series'][0]['points'][0]['y'], 30)          # profile A at 30 dbar, not a mean
        middle = analysis.run_analysis(region='equator', months=[3, 4, 5, 6], stat='median', group_by='none')
        self.assertEqual(middle['series'][0]['points'][0]['y'], 27)       # median of 28, 26, 27

    def test_compare_regions_marks_low_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = analysis.run_analysis(compare_by='region', compare_values=['equator', 'arabian_sea'], group_by='none')
        (eq,), (ar,) = [s['points'] for s in result['series']]
        self.assertEqual((eq['y'], eq['low'], ar['y'], ar['low']), (26.5, False, 30, True))
        self.assertIsNone(result['filter'])
        self.assertEqual(result['title'], 'Mean temperature · Equator vs Arabian Sea')

    def test_profile_counts_and_grids_counted_once(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder, EQUATOR_FIXTURES[:1])
            load_fixtures(self.conn, folder, EQUATOR_FIXTURES[:1], prefix='BD')
        mean = analysis.run_analysis(region='equator', group_by='none')
        self.assertEqual(mean['series'][0]['points'][0]['n_profiles'], 1)
        counts = analysis.run_analysis(measure='profiles', stat='count', group_by='year')
        self.assertEqual([(p['x'], p['y']) for p in counts['series'][0]['points']], [(2024, 1)])
```

(add `import analysis` at the top of the integration module)

- [ ] **Step 2: Run them and confirm they fail** — `-k analysis` / `-k test_sql_is_fixed` → Expected: `ImportError: cannot import name 'run_analysis'` / `AttributeError`.
- [ ] **Step 3: Implement** `run_analysis` for line/bar/profile.
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Run grouped analyses: statistics, groupings, comparisons, low-confidence points"`.

---

### Task 4: Histogram, scatter and map analyses

**Files:**
- Modify: `analysis.py`, `tests/test_etl.py` (`make_fixture` gains `extra`)
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: Task 3.
- Produces: `run_analysis` handles `histogram`, `scatter`, `map`; these results have `series: []` and `points`:
  - histogram: per-profile layer means (`SELECT profile_id, wmo_id, AVG(<col>) AS v … GROUP BY profile_id, wmo_id`, `max_rows=5000`), binned in Python with `numpy.histogram(values, bins=request.bins)` → points `{"x": centre, "x0": left, "x1": right, "y": count}`; `n_profiles` = number of values.
  - scatter: `SELECT profile_id FROM profile_summary WHERE <conditions> ORDER BY random() LIMIT 300`, then `SELECT <x_col> AS x, <col> AS y, pres FROM observations WHERE profile_id = ANY(%s) AND <x_col> IS NOT NULL AND <col> IS NOT NULL AND pres >= %s AND pres <= %s LIMIT 5000` → points `{"x", "y", "pres"}`; note `"A random sample of up to 300 profiles (5,000 levels) is shown."`.
  - map: `SELECT profile_id, wmo_id, MIN(latitude) AS lat, MIN(longitude) AS lon, AVG(<col>) AS value, MIN(date_utc) AS date FROM observations WHERE … GROUP BY profile_id, wmo_id ORDER BY date DESC LIMIT 5000` → points `{"lat", "lon", "value", "profile_id", "wmo_id"}`.
  - `make_fixture(..., extra=None)`: `extra` maps a NetCDF variable name (e.g. `'PSAL'`, `'DOXY'`) to three values; each gets a `<NAME>_QC` of `'1','1','1'`; `99999` is the fill value.
  Status is `no_data` when there are no points; `too_many` on `QueryLimitError`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_histogram_scatter_and_map(self):
        with tempfile.TemporaryDirectory() as folder:
            for cycle, lat, lon, day, temp in EQUATOR_FIXTURES:
                path = Path(folder) / f'D2902115_{cycle:03d}.nc'
                make_fixture(path, temp, latitude=lat, longitude=lon, juld=juld(day), cycle=cycle,
                             extra={'PSAL': [35.0, 35.1, 35.2]})
                self.assertIsNone(etl.process_file(self.conn, path)['error'])
            self.assertTrue(etl.refresh_summary(self.conn))
        hist = analysis.run_analysis(region='equator', chart='histogram', bins=2)
        self.assertEqual((sum(p['y'] for p in hist['points']), hist['chart']), (4, 'histogram'))
        scatter = analysis.run_analysis(region='equator', chart='scatter', measure='temp', x_measure='psal')
        self.assertEqual(len(scatter['points']), 8)                      # 4 profiles × 2 kept levels
        self.assertEqual({p['x'] for p in scatter['points']}, {35.0, 35.2})
        points = analysis.run_analysis(region='equator', chart='map')['points']
        self.assertEqual(sorted(p['value'] for p in points), [25, 26, 27, 28])
```

- [ ] **Step 2: Run it and confirm it fails** — `-k histogram_scatter` → Expected: `TypeError: make_fixture() got an unexpected keyword argument 'extra'`.
- [ ] **Step 3: Implement** `extra` in `make_fixture` and the three analyses.
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Add histogram, scatter and map analyses"`.

---

### Task 5: Diagnosis and data coverage

**Files:**
- Modify: `analysis.py`
- Test: `tests/test_analysis.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `region_phrase` (Task 1), Tasks 3–4.
- Produces:
  - `data_coverage() -> dict` with `floats`, `profiles`, `first_date`, `last_date` (strings), `lat: [min, max]`, `lon: [min, max]`, `measures: {key: non-null count}` (temp…bbp700), `area`: the `label` of the first approximate region whose box contains the centre of the lat/lon range, else `None`. Cached through `functools.lru_cache` on `int(time.time() // 600)` (helper `_coverage(bucket)`; tests call `analysis._coverage.cache_clear()`).
  - `coverage_sentence(cov) -> str`: `"This database has 20 floats in the Indian Ocean area (55.7°S–22.4°N, 11.1°E–137.2°E), 2013-11-24 to 2026-10-18."` (coordinates to 1 decimal; without `area` the words "in the … area" are left out; no profiles → `"The database has no profiles yet."`).
  - `diagnose_empty(request: AnalysisRequest, f: RegionFilter) -> str`: counts on `profile_summary`, adding filters in order: area, float, dates, months; then the measurement on `observations`. First zero wins:
    1. `"No profiles in {phrase} at all."`
    2. `"Float {wmo} has no profiles in {phrase}."`
    3. `"{Phrase} has {n:,} profiles, but none between {a} and {b}."` (or `from {a}` / `until {b}`)
    4. `"{Phrase} has {n:,} profiles, but none in {months label}."`
    5. `"These profiles have no {label} measurements."` or `"These profiles have {label} measurements, but none between {lo:g} and {hi:g} dbar."`
    6. otherwise `"No measurements match this request."`
    (`{Phrase}` is `phrase` with its first letter capitalised; `label` is lower-case.)
  - `coverage` is filled only for `no_data` results (the planner already sees coverage; this avoids an extra query per chart).
  - `run_analysis` with `status == "no_data"` sets `diagnosis` = the distinct sentences for each series (in order, joined by a space) + `" " + coverage_sentence(...)`, and `coverage` = `data_coverage()`.
  - `SUPPORTED_SUMMARY = "I can chart temperature, salinity, oxygen, chlorophyll-a, nitrate, backscatter or profile counts, as a mean, median, minimum, maximum, standard deviation, count or 10th/90th percentile, by day, week, month, year, calendar month, depth, float or latitude/longitude band, for named regions (including approximate ocean boxes) or a latitude/longitude box, and compare up to four regions, floats or years."`

- [ ] **Step 1: Write the failing tests**

Unit:

```python
    def test_coverage_sentence(self):
        cov = dict(floats=20, profiles=3330, first_date='2013-11-24', last_date='2026-10-18',
                   lat=[-55.71447, 22.4463], lon=[11.129, 137.2407], measures={}, area='Indian Ocean')
        self.assertEqual(coverage_sentence(cov), 'This database has 20 floats in the Indian Ocean area '
                         '(55.7°S–22.4°N, 11.1°E–137.2°E), 2013-11-24 to 2026-10-18.')
        self.assertEqual(coverage_sentence(dict(cov, profiles=0)), 'The database has no profiles yet.')
```

Integration (call `analysis._coverage.cache_clear()` in `setUp`):

```python
    def test_pacific_has_no_data_and_says_why(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = analysis.run_analysis(region='pacific_ocean')
        self.assertEqual(result['status'], 'no_data')
        self.assertTrue(result['diagnosis'].startswith('No profiles in the Pacific Ocean (approximate box) at all. This database has 1 float'))

    def test_dates_and_months_diagnoses(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        dated = analysis.run_analysis(region='equator', start_date='2010-01-01', end_date='2010-12-31')
        self.assertIn('The equatorial band (5°S–5°N) has 4 profiles, but none between 2010-01-01 and 2010-12-31.', dated['diagnosis'])
        self.assertIn('but none in Sep.', analysis.run_analysis(region='equator', months=[9])['diagnosis'])
        self.assertIn('Float 1234567 has no profiles in', analysis.run_analysis(region='equator', wmo_id='1234567')['diagnosis'])

    def test_measured_only_deeper(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'D2902115_001.nc'
            make_fixture(path, latitude=0, longitude=80, juld=juld('2024-03-15'), extra={'DOXY': [99999, 200, 210]})
            self.assertIsNone(etl.process_file(self.conn, path)['error'])
            self.assertTrue(etl.refresh_summary(self.conn))
        result = analysis.run_analysis(region='equator', measure='doxy')
        self.assertIn('These profiles have oxygen measurements, but none between 0 and 10 dbar.', result['diagnosis'])
        nitrate = analysis.run_analysis(region='equator', measure='nitrate')
        self.assertIn('These profiles have no nitrate measurements.', nitrate['diagnosis'])
```

- [ ] **Step 2: Run them and confirm they fail** → Expected: `ImportError: cannot import name 'coverage_sentence'` and `diagnosis` is `None`.
- [ ] **Step 3: Implement** `data_coverage`, `coverage_sentence`, `diagnose_empty`, wire into `run_analysis`.
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Explain empty analyses and describe data coverage"`.

---

### Task 6: `theme.py` and `charts.py`

**Files:**
- Create: `theme.py`, `charts.py`
- Modify: `app.py` (import tokens from `theme`)
- Test: `tests/test_charts.py` (new)

Load the `dataviz` skill first, and run its validator on the series colours:
`node <dataviz>/scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500" --mode dark` (surface `#18181b`) → Expected: no FAIL.

**Interfaces:**
- `theme.py` (moved from `app.py`, unchanged values): `FONT`, `MONO`, `SURFACE_2`, `BORDER`, `GRID`, `INK`, `INK_2`, `MUTED`, `ACCENT`, `HIGHLIGHT`, `PROFILE_DOT`, `COLORS`, `PRESSURE_SCALE`, `AXIS`, `BASE_LAYOUT`, `placeholder_figure`, `plural`, `translucent`; new `SERIES_COLORS = ("#3987e5", "#d95926", "#199e70", "#c98500")`. `app.py` imports them.
- `charts.py`:
  - `build_chart(result: dict) -> go.Figure`: status `no_data`/`too_many`/`unsupported` → `placeholder_figure("", height=40)`. Otherwise by `result["chart"]`:
    - `line`/`bar`/`profile`: one trace per series; colour `COLORS[measure]` (or `ACCENT` for `profiles`) when one series, `SERIES_COLORS[i]` otherwise; 2 px lines, 8 px markers; low points get `marker.symbol "circle-open"` and opacity 0.45 (per-point arrays); `month_of_year` x labels via `MONTH_NAMES`, category axis; `day`/`week`/`month` date axis; bars `barmode="group"`; profile → x = value, y = bucket + `depth_bin`/2, reversed y; legend only with >1 series (horizontal, above the plot).
    - `histogram`: one `go.Bar` (x = centres, width = `x1 - x0`, y = counts), y title `"Profiles"`.
    - `scatter`: markers coloured by `pres` with `PRESSURE_SCALE` and a `dbar` colour bar.
    - `map`: `go.Scattermap` coloured by `value` with `PRESSURE_SCALE`, colour bar in `units`, style `carto-darkmatter`, centred on the points' mean, zoom 2.
    - Value-axis title: `"{STAT_LABELS[stat]} {label.lower()} ({units}), {lo:g}–{hi:g} dbar"`; profile x title without the layer; `profiles` → `"Number of profiles"`. Height 260 (map 300), `BASE_LAYOUT`.
  - `describe_result(result: dict) -> dict` with `title` (`result["title"]`, or `"No analysis"` for `unsupported`), `caption` (joined by `" · "`: `"Average of each profile's mean, {lo:g}–{hi:g} dbar"` for `mean` — or `"{STAT_LABELS[stat]} per group, {lo:g}–{hi:g} dbar"` otherwise —, `plural(n_profiles, "profile")` + `" from "` + `plural(n_floats, "float")` for single-series results, the date span, then the notes; `""` for the message statuses below), `message` (`diagnosis` for `no_data`; `"Too many points to chart. Choose a coarser grouping or a shorter period."` for `too_many`; `reason` + `" " + supported` for `unsupported`; else `None`).

- [ ] **Step 1: Write the failing tests** in `tests/test_charts.py`

```python
def series(name, points):
    return dict(name=name, points=points, n_profiles=sum(p['n_profiles'] for p in points), n_floats=1,
                first_date='2024-03-01', last_date='2024-06-30')

BASE = dict(status='ok', title='Mean temperature by calendar month · Equator', measure='temp', measure_label='Temperature',
            units='°C', stat='mean', group_by='month_of_year', chart='line', pressure_range=[0, 10], depth_bin=50,
            notes=[], diagnosis=None, n_profiles=6, filter={'region': 'equator'}, filter_label='Equator')
P = lambda x, y, n=3: dict(x=x, y=y, n_profiles=n, n_floats=1, low=n < 3)

class ChartTests(unittest.TestCase):
    def test_line_with_month_labels_and_layer_title(self):
        fig = charts.build_chart(dict(BASE, series=[series('Equator', [P(3, 28), P(4, 26)])]))
        self.assertEqual(list(fig.data[0].x), ['Mar', 'Apr'])
        self.assertEqual(fig.layout.yaxis.title.text, 'Mean temperature (°C), 0–10 dbar')
        self.assertFalse(fig.layout.showlegend)

    def test_compare_uses_series_colours_and_legend(self):
        fig = charts.build_chart(dict(BASE, series=[series('A', [P(3, 28)]), series('B', [P(3, 29)])]))
        self.assertEqual([t.line.color for t in fig.data], list(charts.SERIES_COLORS[:2]))
        self.assertTrue(fig.layout.showlegend)

    def test_low_points_are_faded(self):
        fig = charts.build_chart(dict(BASE, series=[series('E', [P(3, 28), P(4, 26, n=1)])]))
        self.assertEqual(list(fig.data[0].marker.opacity), [1, 0.45])
        self.assertEqual(list(fig.data[0].marker.symbol), ['circle', 'circle-open'])

    def test_profile_histogram_scatter_map(self):
        prof = charts.build_chart(dict(BASE, chart='profile', group_by='depth', pressure_range=[0, 2000],
                                       series=[series('E', [P(0.0, 29), P(50.0, 25)])]))
        self.assertEqual((list(prof.data[0].y), prof.layout.yaxis.autorange), ([25.0, 75.0], 'reversed'))
        hist = charts.build_chart(dict(BASE, chart='histogram', series=[], points=[dict(x=26.5, x0=26, x1=27, y=2)]))
        self.assertEqual((hist.data[0].type, list(hist.data[0].y)), ('bar', [2]))
        sc = charts.build_chart(dict(BASE, chart='scatter', x_measure='psal', series=[], points=[dict(x=35, y=28, pres=10)]))
        self.assertEqual(sc.data[0].mode, 'markers')
        mp = charts.build_chart(dict(BASE, chart='map', series=[], points=[dict(lat=0, lon=80, value=28, profile_id=1, wmo_id='1')]))
        self.assertEqual(mp.data[0].type, 'scattermap')

    def test_messages_instead_of_charts(self):
        no_data = dict(BASE, status='no_data', series=[], diagnosis='No profiles in the Pacific Ocean (approximate box) at all.')
        self.assertEqual(charts.describe_result(no_data)['message'], no_data['diagnosis'])
        self.assertIn('Too many points', charts.describe_result(dict(BASE, status='too_many', series=[]))['message'])
        caption = charts.describe_result(dict(BASE, series=[series('E', [P(3, 28), P(4, 26)])]))['caption']
        self.assertIn("Average of each profile's mean, 0–10 dbar · 6 profiles from 1 float · 2024-03-01 to 2024-06-30", caption)
```

- [ ] **Step 2: Run them and confirm they fail** → Expected: `ModuleNotFoundError: No module named 'charts'`.
- [ ] **Step 3: Implement** `theme.py` (move), `charts.py`; `app.py` imports from `theme`.
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Add charts.py for every analysis chart type; move design tokens to theme.py"`.

---

### Task 7: Switch the app, chat actions and MCP to `analyze`

**Files:**
- Modify: `ocean_tools.py`, `chat_service.py` (`dashboard_actions` only), `postgres_mcp.py`, `app.py`
- Test: `tests/test_chat.py`, `tests/test_mcp.py`, `tests/test_app.py`, `tests/test_regions.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: `AnalysisRequest`, `run_analysis`, `data_coverage`, `SUPPORTED_SUMMARY` (Tasks 2–5); `build_chart`, `describe_result` (Task 6).
- Produces:
  - `ocean_tools.TOOLS`: remove `aggregate_measurements` (and `AggregateArgs`, `VARIABLES`, `BUCKETS`, the function); add
    `"analyze": (AnalysisRequest, run_analysis, "Compute and chart any statistic of a measurement over matching profiles: measure, stat, group_by, compare_by/compare_values, filters, chart. Use for averages, trends, distributions, comparisons and any graph.")`,
    `"data_coverage": (SummaryArgs, data_coverage, "What the database covers: floats, dates, area and how many values each measurement has.")`,
    `"unsupported": (UnsupportedArgs, unsupported, "Use when no operation can answer the question (derived quantities, forecasts, data outside this database).")`.
    `UnsupportedArgs(reason: str = Field(min_length=1, max_length=300))`; `unsupported(reason) -> {"status": "unsupported", "reason": reason, "supported": SUPPORTED_SUMMARY, "chart": "none", "series": []}`. `ToolPlan.tool` lists the nine tool names.
  - `dashboard_actions`: `analyze` → `{"chart": result, "highlight": {"filter": result["filter"], "label": result["filter_label"]} if result.get("filter") is not None and result.get("n_profiles") else None}`; `unsupported` → `{"chart": result}`; `region_profiles` and `select` unchanged.
  - `postgres_mcp.py`: replace `aggregate_measurements` with `analyze(measure="temp", stat="mean", group_by="month", compare_by="none", compare_values=None, x_measure=None, chart="auto", region=None, lat_min=None, lat_max=None, lon_min=None, lon_max=None, start_date=None, end_date=None, months=None, wmo_id=None, pres_min=None, pres_max=None, depth_bin=50, band_deg=5, bins=30) -> str` and add `data_coverage() -> str`; both forward non-`None` arguments.
  - `app.py`: delete `build_analysis_chart`; panel layout adds `html.P(id="analysis-message", className="analysis-message")` and wraps the graph in `html.Div(dcc.Graph(...), id="analysis-graph")`; `render_analysis(result) -> (figure, title, caption, message, graph_hidden, panel_hidden)` built from `build_chart` + `describe_result` (`graph_hidden` = message is not `None`); `region_chart` calls `run_analysis(region=…, months=… or None, measure=variable, group_by=…)` (no `QueryLimitError` branch any more) and routes through `dashboard_actions(ToolPlan(tool="analyze", arguments={}), result)`; imports `MEASURES` instead of `VARIABLES` (the measurement dropdown lists `MEASURES` without `profiles` until Task 8).
  - CSS: `.analysis-message { margin: 8px 0 4px; color: var(--ink-2); line-height: 1.5; }`.
- Tests to delete (each replaced by Task 3–6 tests or the new ones below): in `test_regions.py` the three `aggregate` tests; in `test_integration.py` the four `test_aggregate_*` tests; in `test_app.py` `test_analysis_chart_time_series`, `test_analysis_chart_pressure_is_reversed`, `test_analysis_chart_empty`, and the `aggregate_measurements` patches in the `region_chart` tests (patch `app.run_analysis` instead); in `test_chat.py` / `test_mcp.py` the `aggregate_measurements` references.

- [ ] **Step 1: Write the failing tests**

```python
# test_chat.py
    def test_analyze_and_unsupported_actions(self):
        plan = lambda tool: chat.ToolPlan(tool=tool, arguments={})
        result = {'status': 'ok', 'n_profiles': 3, 'filter': {'region': 'equator'}, 'filter_label': 'Equator', 'series': []}
        self.assertEqual(chat.dashboard_actions(plan('analyze'), result),
                         {'chart': result, 'highlight': {'filter': {'region': 'equator'}, 'label': 'Equator'}})
        compared = dict(result, filter=None)
        self.assertIsNone(chat.dashboard_actions(plan('analyze'), compared)['highlight'])
        refused = execute_tool('unsupported', {'reason': 'Mixed-layer depth is not available.'})
        self.assertEqual(chat.dashboard_actions(plan('unsupported'), refused), {'chart': refused})
        self.assertEqual(refused['status'], 'unsupported')

# test_mcp.py (replace the aggregate assertions)
            postgres_mcp.analyze(region='pacific_ocean', stat='max')
            name, arguments = run.call_args.args
        self.assertEqual((name, arguments['stat'], arguments['region']), ('analyze', 'max', 'pacific_ocean'))
        self.assertNotIn('pres_max', arguments)

# test_app.py
    def test_render_analysis_shows_messages_and_charts(self):
        no_data = dict(status='no_data', title='Mean temperature · Pacific Ocean (approx.)', series=[], notes=[],
                       diagnosis='No profiles in the Pacific Ocean (approximate box) at all.', stat='mean',
                       pressure_range=[0, 10], measure='temp', chart='line')
        _, _, _, message, graph_hidden, panel_hidden = app.render_analysis(no_data)
        self.assertEqual((message, graph_hidden, panel_hidden), (no_data['diagnosis'], True, False))
        self.assertTrue(app.render_analysis(None)[5])

    def test_region_chart_uses_the_analysis_engine(self):
        result = {'status': 'ok', 'n_profiles': 2, 'filter': {'region': 'equator'}, 'filter_label': '5°S–5°N', 'series': []}
        with patch('app.run_analysis', return_value=result) as run:
            chart, highlight, feedback = app.region_chart(1, 'equator', [3, 4], 'temp', 'month_of_year')
        run.assert_called_once_with(region='equator', months=[3, 4], measure='temp', group_by='month_of_year')
        self.assertEqual((chart, highlight['label'], feedback), (result, '5°S–5°N', ''))
```

- [ ] **Step 2: Run them and confirm they fail** → Expected: `ValueError: Unknown data operation.` (no `analyze`), `AttributeError` for `postgres_mcp.analyze`, `render_analysis` returning 4 values.
- [ ] **Step 3: Implement** the switch-over and delete the replaced tests and code.
- [ ] **Step 4: Run the whole suite and grep for leftovers**
  - `.venv/bin/python -m unittest discover -s tests` → Expected: `OK`.
  - `grep -rn "aggregate_measurements\|AggregateArgs\|build_analysis_chart" --include='*.py' . | grep -v .venv` → Expected: no output.
- [ ] **Step 5: Commit** — `"Replace aggregate_measurements with the analyze engine across chat, app and MCP"`.

---

### Task 8: The Analyze tab

**Files:**
- Modify: `app.py` (pane and callback), `assets/style.css` (only if the extra fields need it)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `REGIONS`, `MEASURES`, `STAT_LABELS`, `GROUP_LABELS`, `run_analysis`, `dashboard_actions`.
- Produces:
  - Tab label `"Analyze"` (ids `tab-regions`/`pane-regions` unchanged). Help text: `"Chart any statistic for a region, or highlight its profiles. No AI key needed."`
  - Fields: `region-select` (all `REGIONS`, label `REGIONS[k]["label"]` + `" (approx.)"` when approximate; default `equator`), `months-select` (unchanged), `variable-select` (all `MEASURES`, `profiles` labelled `"Profile count"`; default `temp`), new `stat-select` (`STAT_LABELS` keys with labels Mean, Median, Minimum, Maximum, Standard deviation, Count, 10th percentile, 90th percentile; default `mean`), `group-select` (all `GROUP_LABELS`; labels `"None (one value)"`, `"By day"`, `"By week"`, `"By month"`, `"By year"`, `"By calendar month"`, `"By depth"`, `"By float"`, `"By latitude band"`, `"By longitude band"`; default `month_of_year`), new `chart-select` (`auto` "Automatic", line, bar, scatter, histogram, profile "Depth profile", map), new `x-measure-select` ("Against (scatter)", the six measurements, clearable, default `None`).
  - Buttons: `region-highlight-btn` ("Highlight"), `region-chart-btn` ("Chart").
  - `region_chart(n_clicks, region, months, measure, group_by, stat="mean", chart="auto", x_measure=None)` passes all non-`None` fields to `run_analysis` (with `months or None`). A Pydantic validation error → feedback `"That combination isn't supported: {first error message without the 'Value error, ' prefix}"`; other errors as before.

- [ ] **Step 1: Write the failing tests**

```python
    def test_analyze_tab_passes_every_field(self):
        result = {'status': 'ok', 'n_profiles': 1, 'filter': {'region': 'pacific_ocean'}, 'filter_label': 'x', 'series': []}
        with patch('app.run_analysis', return_value=result) as run:
            app.region_chart(1, 'pacific_ocean', [], 'psal', 'none', 'max', 'scatter', 'temp')
        run.assert_called_once_with(region='pacific_ocean', measure='psal', group_by='none', stat='max',
                                    chart='scatter', x_measure='temp')

    def test_analyze_tab_explains_unsupported_combinations(self):
        feedback = app.region_chart(1, 'equator', [], 'profiles', 'month', 'mean', 'auto', None)[2]
        self.assertEqual(feedback, "That combination isn't supported: Profile counts use stat count.")

    def test_analyze_tab_lists_oceans(self):
        def find(node, target):
            if getattr(node, 'id', None) == target:
                return node
            children = getattr(node, 'children', None)
            for child in children if isinstance(children, list) else [children] if children is not None else []:
                found = find(child, target)
                if found is not None:
                    return found
        labels = [o['label'] for o in find(app.app.layout, 'region-select').options]
        self.assertIn('Pacific Ocean (approx.)', labels)
        self.assertEqual(find(app.app.layout, 'tab-regions').children[1], 'Analyze')
```

Also update Task 7's `test_region_chart_uses_the_analysis_engine`: the expected call now includes `stat='mean', chart='auto'`.

- [ ] **Step 2: Run them and confirm they fail** → Expected: `TypeError` (extra positional arguments) and missing `Pacific Ocean (approx.)`.
- [ ] **Step 3: Implement** the pane and callback (add the three new `State`s).
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Turn the Regions tab into an Analyze tab with every analysis option"`.

---

### Task 9: Chat planning: coverage, hints, one repair, honest answers

**Files:**
- Modify: `chat_service.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `data_coverage`, `coverage_sentence`, `SUPPORTED_SUMMARY` from `analysis`.
- Produces:
  - Planning prompt additions, verbatim: `"Named regions: equator (5°S–5°N), arabian_sea, bay_of_bengal, indian_ocean, pacific_ocean, atlantic_ocean, southern_ocean, arctic_ocean (oceans are approximate boxes); or explicit lat_min/lat_max/lon_min/lon_max. For any chart, graph, average, trend, distribution or comparison use analyze: pick measure, stat, group_by, chart; 'compare A and B' uses compare_by and compare_values. For months without a year, such as 'March to June', use months [3,4,5,6]. Use region_profiles to find, count or highlight all floats in an area. If no operation can answer, use unsupported with a short reason."` followed by `"Data coverage: " + coverage_sentence(data_coverage())` (omitted if that raises).
  - `class PlanError(Exception)`; `plan_and_execute(system: str, payload: str, *, api_key=None, model=None) -> tuple[ToolPlan, dict]`: generate → `parse_plan` → `execute_tool`; on `pydantic.ValidationError` or a `ValueError` that is not a `QueryLimitError`, call `generate` once more with the same system prompt and `payload + "\nYOUR PREVIOUS PLAN WAS INVALID: " + <problems> + "\nReturn a corrected plan."`, where `<problems>` is `"; ".join("<loc>: <msg>")` from `exc.errors()` (no input values) or `str(exc)` for plain `ValueError`s; a second failure raises `PlanError`.
  - `answer_question` uses `plan_and_execute`; `PlanError` → returns `("I couldn't turn that into a supported analysis. " + SUPPORTED_SUMMARY, None)`.
  - Answer prompt additions, verbatim: `"If the result has a status other than ok, lead with it: for no_data give the diagnosis; for low_data say the points rest on very few profiles; for too_many suggest a coarser grouping; for unsupported say it can't be computed and what can. State the depth layer, date span and profile/float counts. Mention when region boxes are approximate."`

- [ ] **Step 1: Write the failing tests**

```python
    def test_invalid_plan_is_repaired_once(self):
        good = {'status': 'ok', 'n_profiles': 1, 'filter': {}, 'filter_label': 'All profiles', 'series': []}
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.coverage_sentence', return_value='This database has 20 floats.'), \
             patch('chat_service.data_coverage', return_value={}), \
             patch('chat_service.generate', side_effect=['{"tool":"analyze","arguments":{"measure":"profiles"}}',
                                                         '{"tool":"analyze","arguments":{"measure":"profiles","stat":"count","group_by":"year"}}',
                                                         'One profile.']) as model, \
             patch('chat_service.execute_tool', side_effect=[ValueError('Profile counts use stat count.'), good]):
            answer, actions = chat.answer_question('How many profiles per year?')
        self.assertEqual(model.call_count, 3)
        self.assertIn('YOUR PREVIOUS PLAN WAS INVALID: Profile counts use stat count.', model.call_args_list[1].args[1])
        self.assertIn('Data coverage: This database has 20 floats.', model.call_args_list[0].args[0])
        self.assertEqual((answer, actions['chart']), ('One profile.', good))

    def test_second_invalid_plan_gives_up_politely(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.data_coverage', side_effect=RuntimeError('db down')), \
             patch('chat_service.generate', side_effect=['not json', '{"tool":"query_data","arguments":{}}']) as model:
            answer, actions = chat.answer_question('Draw something')
        self.assertEqual(model.call_count, 2)
        self.assertTrue(answer.startswith("I couldn't turn that into a supported analysis. I can chart"))
        self.assertIsNone(actions)

    def test_prompts_cover_charts_and_honesty(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.data_coverage', return_value={}), patch('chat_service.coverage_sentence', return_value=''), \
             patch('chat_service.generate', side_effect=['{"tool":"unsupported","arguments":{"reason":"No forecasts."}}', 'Sorry.']) as model:
            chat.answer_question('Forecast next year')
        plan_prompt, answer_prompt = model.call_args_list[0].args[0], model.call_args_list[1].args[0]
        self.assertIn('pacific_ocean', plan_prompt)
        self.assertIn('use unsupported with a short reason', plan_prompt)
        self.assertIn('for no_data give the diagnosis', answer_prompt)
```

Update `test_prompts_describe_regions_and_dashboard` to the new wording (keep its `'March to June'` and `'5°S–5°N'` assertions). Give `ChatTests` a `setUp` that patches `chat_service.data_coverage` to raise `RuntimeError` (stopped in `tearDown`), so no chat unit test can reach a real database; tests that need coverage patch it again inside the test.

- [ ] **Step 2: Run them and confirm they fail** → Expected: `AttributeError: … has no attribute 'coverage_sentence'` / call counts differ.
- [ ] **Step 3: Implement** prompts, `PlanError`, `plan_and_execute`.
- [ ] **Step 4: Run the whole suite** → Expected: `OK`.
- [ ] **Step 5: Commit** — `"Plan any chart from English with coverage, one repair and honest answers"`.

---

### Task 10: Docs and end-to-end check

**Files:**
- Modify: `README.md`, `docs/project-guide.md`, `app.py` (`SUGGESTIONS`)

- [ ] **Step 1: Suggestions.** Replace the last suggestion with `"Compare oxygen in the Arabian Sea and Bay of Bengal by month"` and add `"Mean temperature in the Pacific Ocean"`.
- [ ] **Step 2: Real-data check** against the local 20-float database (reader role), timing each:

```bash
DB_HOST=127.0.0.1 DB_PORT=55432 DB_NAME=floatchat DB_USER=floatchat_reader DB_PASS=local-reader DB_SSLMODE=disable \
.venv/bin/python -c "
import time, analysis
for req in [dict(region='pacific_ocean'), dict(compare_by='region', compare_values=['arabian_sea','bay_of_bengal'], measure='doxy'),
            dict(wmo_id='2902114', chart='profile', measure='psal'), dict(chart='histogram', start_date='2024-01-01', end_date='2024-12-31'),
            dict(stat='max', measure='chla', group_by='year'), dict(chart='scatter', measure='temp', x_measure='psal', region='equator'),
            dict(chart='map', region='indian_ocean'), dict(group_by='day'), dict(measure='profiles', stat='count', group_by='lat_band')]:
    s = time.time(); r = analysis.run_analysis(**req)
    print(round(time.time() - s, 2), r['status'], r['title'], (r['diagnosis'] or '')[:90])
"
```

Expected: every line under 10 s; Pacific → `no_data` with the Pacific diagnosis; nothing raises.

- [ ] **Step 3: Browser check** (built-in browser, local app on the same database): Analyze tab — line, compare-free bar, depth profile, histogram, scatter (with "Against"), map, and Pacific (message instead of chart); an unsupported combination shows the feedback line; phone width stacks without horizontal scroll. With the user's own OpenRouter key (the user pastes it; Claude never types it): the five spec example questions.
- [ ] **Step 4: Docs.** Project guide §10 (Analyze tab, panel message), §11 (tool table: `analyze`, `data_coverage`, `unsupported`; repair step; ≤3 calls), §12 (MCP tools), §17 (oceans are approximate boxes; no AI-written SQL by design), §18 (adding a measure: `MEASURES`; a region: `REGIONS`). README: features and example questions.
- [ ] **Step 5: Full suite and commit** — `.venv/bin/python -m unittest discover -s tests` → `OK`; commit `"Document natural-language charts and add example suggestions"`.

## Not in this plan

AI-written SQL; derived quantities (density, mixed-layer depth, heat content) — the agreed next spec; exact ocean borders; pushing, PR or deployment (ask first).
