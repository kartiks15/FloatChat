# Natural-language charts: design

**Status:** Section 1 approved in chat on 18 October 2026; this document adds sections 2–5 for
review. **Branch:** `feature/region-analysis` (builds on the region work there).

## Goal

A user asks any data question in English, such as "chart mean temperature in the Pacific
Ocean", "compare oxygen in the Arabian Sea and Bay of Bengal by month", "salinity vs depth for
float 2902115", or "histogram of surface temperatures in 2024". The AI decides what to compute
and which chart to draw. The app draws it, highlights the data on the map, and **says plainly
when the data cannot answer** (no data, too little data, or a question the operations cannot
express) instead of answering something else.

The AI never writes SQL. It fills in one validated **analysis request**; the server compiles it
to parameterised SQL from fixed pieces.

## Success criteria

1. Each example question in this document produces the described chart, or the described
   honest refusal, through both the chat and the MCP server.
2. "Mean temperature in the Pacific Ocean" against the current data (Indian Ocean only) answers
   that there are no profiles in the Pacific and states what the database covers.
3. No SQL text from the model or the browser ever reaches the database; every value is a bound
   parameter (unit-tested on the compiled SQL).
4. Every analysis finishes under the 10 s statement timeout on the current 2.7 M-observation
   database, or returns a "too much to chart, narrow it" message.
5. At most three model calls per question.

## 1. The analysis request (approved)

| Part | Field | Choices |
| --- | --- | --- |
| Measurement | `measure` | `temp`, `psal`, `doxy`, `chla`, `nitrate`, `bbp700`, `profiles` (number of profiles) |
| Statistic | `stat` | `mean` (default), `median`, `min`, `max`, `std`, `count`, `p10`, `p90` |
| Group by | `group_by` | `none`, `day`, `week`, `month` (default), `year`, `month_of_year`, `depth`, `float`, `lat_band`, `lon_band` |
| Compare | `compare_by`, `compare_values` | `none` (default), `region`, `float`, `year`; up to 4 values |
| Second measurement | `x_measure` | Scatter only: the x-axis measurement |
| Filters | region fields | `region` (named) or `lat_min`/`lat_max`/`lon_min`/`lon_max`; `start_date`, `end_date`, `months`, `wmo_id`; `pres_min`, `pres_max` |
| Binning | `depth_bin`, `band_deg`, `bins` | depth bin 10–500 dbar (default 50); band 1–20° (default 5); histogram bins 5–60 (default 30) |
| Chart | `chart` | `auto` (default), `line`, `bar`, `scatter`, `histogram`, `profile`, `map` |

**How statistics are computed.** Each profile first contributes one value per group: its mean
within the depth layer (so profiles with many levels, or stored as separate core and BGC grids,
count once), plus its raw minimum and maximum. Then, per group:
`mean`, `median`, `std`, `p10`, `p90` are taken over the profile means; `min` and `max` over the
raw extremes (the true observed extremes); `count` is the number of profiles.
`measure=profiles` requires `stat=count`.

**Default depth layer.** 0–10 dbar ("surface") for every chart except `profile` (0–2,000 dbar)
and `scatter` (all depths). `pres_min`/`pres_max` override it. The layer is always stated in the
axis title and caption.

**Named regions.** The existing `equator` (5°S–5°N), `arabian_sea`, `bay_of_bengal`, plus
approximate ocean boxes, labelled "approximate" wherever they appear:

| Region | Latitude | Longitude |
| --- | --- | --- |
| `indian_ocean` | 60°S–30°N, plus 60°S–30°S | 20°E–120°E, plus 120°E–147°E (south of Australia) |
| `pacific_ocean` | 60°S–60°N, plus 30°S–60°N | 147°E–70°W (crosses ±180°), plus 120°E–147°E (north of 30°S) |
| `atlantic_ocean` | 60°S–65°N | 70°W–20°E |
| `southern_ocean` | 90°S–60°S | all |
| `arctic_ocean` | 66°N–90°N | all |

(Amended during implementation: south of Australia the Indian/Pacific boundary runs down from
Tasmania at ~147°E, so each of those oceans is two boxes; still boxes, not polygons.)

**Combination rules** (rejected with a clear message otherwise):

- `chart=profile` means `group_by=depth`; `chart=scatter` requires `x_measure` and ignores
  `group_by`; `chart=histogram` and `chart=map` ignore `group_by`.
- `compare_by=region` takes named regions; `float` takes WMO IDs; `year` takes years. With
  `compare_by=year`, `group_by` must be `month_of_year` or `none`, so the years share one x-axis.
- `chart=auto` picks: depth → `profile`; `day`/`week`/`month`/`year`/`month_of_year` → `line`;
  `none`/`float`/`lat_band`/`lon_band` → `bar`.

## 2. Architecture

```
chat_service.py ──plan──▶ ocean_tools.execute_tool("analyze", args)
                              │
regions.py   ◀── analysis.py ─┘   (AnalysisRequest → SQL → result with status/diagnosis)
   ▲               │
ocean_tools.py     └──▶ result dict ──▶ app.py Analysis panel ──▶ charts.py build_chart(result)
postgres_mcp.py: analyze, data_coverage
```

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `regions.py` (new, moved out of `ocean_tools.py`) | `REGIONS` (now with oceans and an `approximate` flag), `RegionFilter`, `region_conditions`, `describe_filter`, `filter_dict`, `MONTH_NAMES` | — |
| `analysis.py` (new) | `AnalysisRequest` model and its combination rules; SQL compilation from fixed fragments; `run_analysis(**request) -> dict`; empty-result diagnosis; low-data marking; `data_coverage()` (cached 10 minutes) | `regions.py`, `database.py` |
| `ocean_tools.py` | Tool registry. `aggregate_measurements` is **replaced** by `analyze`; adds `data_coverage` and `unsupported`. Re-exports the region names so existing imports keep working. | `regions.py`, `analysis.py` |
| `chat_service.py` | Planning prompt gains a compact coverage summary and the analysis vocabulary; one repair attempt for an invalid plan; answer prompt rules for `status`; `dashboard_actions` handles `analyze`. | `ocean_tools.py` |
| `theme.py` (new, moved out of `app.py`) | Design tokens and shared Plotly layout (`AXIS`, `BASE_LAYOUT`, `COLORS`, `placeholder_figure`). | — |
| `charts.py` (new) | `build_chart(result) -> go.Figure` for all six chart types, multi-series, low-confidence marking, and the no-data message. Replaces `build_analysis_chart` in `app.py`. | `theme.py` |
| `app.py` | Analysis panel renders any result; Regions tab becomes **Analyze** with region, months, measurement, statistic, group-by and chart controls calling `run_analysis`. | all of the above |
| `postgres_mcp.py` | `analyze` and `data_coverage` tools (replacing `aggregate_measurements`). | `ocean_tools.py` |

### SQL compilation

Fixed fragments keyed by `Literal` values; nothing else is interpolated.

- **Per-profile step** (all charts but scatter):
  `SELECT profile_id, wmo_id, <group expr> AS g, AVG(col) AS v, MIN(col) AS lo, MAX(col) AS hi FROM observations WHERE col IS NOT NULL AND pres BETWEEN %s AND %s AND <region conditions> GROUP BY profile_id, wmo_id, g`.
  Group expressions: `date_trunc` + `to_char` for day/week/month/year,
  `EXTRACT(MONTH …)::int`, `FLOOR(pres / %s) * %s`, `wmo_id`, `FLOOR(latitude / %s) * %s`,
  `FLOOR(longitude / %s) * %s`, or a constant for `none`.
- **Group step:** `AVG(v)`, `percentile_cont(q) WITHIN GROUP (ORDER BY v)`, `STDDEV_SAMP(v)`,
  `MIN(lo)`, `MAX(hi)`, `COUNT(*)`, plus `COUNT(DISTINCT wmo_id)`.
- **`measure=profiles`:** counts from `profile_summary` (no observation scan).
- **Histogram:** per-profile layer means, bucketed with `width_bucket` over their min–max.
- **Scatter:** observations from a random sample of up to 300 matching profiles
  (`ORDER BY random()` on `profile_summary`, cheap), capped at 5,000 points, disclosed as a
  sample.
- **Map:** per-profile layer values with positions, up to 5,000 profiles, newest first.
- **Compare:** one query per series (at most 4), each with that series' extra condition
  (region box, `wmo_id`, or `EXTRACT(YEAR …)`), so overlapping regions are handled correctly.
- **Caps:** at most 1,000 points per series. `run_analysis` catches the resulting
  `QueryLimitError` and returns `status: too_many` ("too many points to chart — choose a coarser
  grouping or a shorter period").

### Result shape

```jsonc
{
  "status": "ok | no_data | low_data | too_many",   // "unsupported" comes only from the unsupported tool
  "title": "Mean temperature by month · Arabian Sea vs Bay of Bengal",
  "measure": "doxy", "measure_label": "Oxygen", "units": "µmol/kg", "stat": "mean",
  "group_by": "month", "chart": "line", "pressure_range": [0, 10],
  "series": [{"name": "Arabian Sea", "points": [{"x": "2024-03", "y": 201.4, "n_profiles": 12,
              "n_floats": 2, "low": false}], "n_profiles": 120, "n_floats": 3,
              "first_date": "2023-01-04", "last_date": "2026-09-30"}],
  "diagnosis": null,
  "notes": ["Region boxes are approximate."],
  "coverage": {"floats": 20, "profiles": 3330, "first_date": "2013-11-24", "last_date": "2026-10-18",
               "lat": [-55.7, 22.4], "lon": [11.1, 137.2],
               "measures": {"temp": 2729411, "doxy": 808111, "nitrate": 103599}},
  "filter": {"region": "arabian_sea"},
  "dashboard": "The dashboard charts these values and highlights the profiles on the map."
}
```

Scatter and map results carry `points` with `x`/`y` (scatter) or `lat`/`lon`/`value` (map)
instead of grouped series points.

## 3. Honesty: no data, little data, unsupported

- **`no_data`.** When every series is empty, `diagnose_empty(request)` runs cheap counts on
  `profile_summary` (and one on `observations` for the measurement), relaxing filters in this
  order, and reports the first step that reaches zero:
  1. area → "No profiles in the Pacific Ocean (approximate box) at all."
  2. + float → "Float 2902115 has no profiles in that area."
  3. + dates → "The Arabian Sea has 412 profiles, but none between 2010-01-01 and 2010-12-31."
  4. + months → "… but none in March–June."
  5. measurement → "These profiles have no nitrate measurements." or, when it exists at other
     depths, "… no nitrate between 0 and 10 dbar; it was measured deeper."

  The diagnosis is followed by one coverage sentence from `data_coverage()`:
  "This database has 20 floats in the Indian Ocean area (55.7°S–22.4°N, 11.1°E–137.2°E),
  2013-11-24 to 2026-10-18."
- **`low_data`.** Points from fewer than 3 profiles get `low: true` and are drawn hollow and
  faded; the caption says so. If every point is low, `status` is `low_data` and the answer leads
  with that.
- **`too_many`.** Raised when a series would exceed 1,000 points; nothing is charted.
- **`unsupported`.** A new tool `unsupported(reason)` the planner must choose when no operation
  can answer (for example mixed-layer depth, forecasts, data from outside this database). It
  returns `{"status": "unsupported", "reason": …, "supported": <one-line summary>}`, and the
  answer says what was asked could not be computed and what can be.
- **Answer prompt rules:** lead with the status when it isn't `ok`; never describe a trend from
  `low_data` points as reliable; state the depth layer, the date span, profile and float counts;
  mention approximate region boxes.

## 4. Chat, dashboard and MCP

- **Planning call** gets the vocabulary (via the tool catalogue, now including `analyze`'s JSON
  Schema), a compact coverage line, and mapping hints ("Pacific → `region: pacific_ocean`";
  "compare A and B → `compare_by`"; "nothing fits → `unsupported`").
- **Repair:** if the plan fails validation, the planner gets one more call with the validation
  messages (field names and reasons only) and must return a corrected plan. A second failure
  answers "I couldn't turn that into a supported analysis" with a short list of what is supported.
  At most three model calls per question; the quota still counts one reservation per question.
- **Dashboard actions:** `analyze` returns `chart` (always) and `highlight` (the base filter when
  any profile matched; `None` otherwise, clearing an old highlight). `select` is unchanged.
- **Analysis panel** renders every chart type through `charts.build_chart`. For `no_data`,
  `too_many` and `unsupported` it shows the message instead of an empty chart. Multi-series charts
  use the categorical palette in fixed order with a legend and direct labels; the map chart uses a
  single-hue sequential scale (validated with the dataviz checks).
- **Analyze tab** (renamed from Regions): region (incl. oceans), months, measurement, statistic,
  group-by, chart; buttons Highlight and Chart. No AI needed.
- **MCP:** `analyze(**request)` and `data_coverage()` replace `aggregate_measurements`;
  `find_region_profiles` stays.

## 5. Testing

- **Unit (`tests/test_analysis.py`):** every combination rule; compiled SQL contains only fixed
  fragments and `%s` (assert no request values appear in the SQL text); `chart=auto` choices;
  low-data marking; diagnosis order with mocked counts; `too_many` mapping.
- **Integration (real Postgres fixtures):** mean by month; `max` returns the raw maximum, not a
  profile mean; median; compare by region with overlapping boxes; histogram bin counts; scatter
  sample cap; map points; Pacific → `no_data` with the "no profiles in the Pacific" diagnosis;
  months diagnosis; "measured deeper" diagnosis; `low_data` flag.
- **Chat:** repair call happens once and only on invalid plans; `unsupported` path; coverage line
  in the planning prompt; `dashboard_actions` for `analyze`.
- **App/charts:** each chart type builds; multi-series legend; no-data message in the panel;
  Analyze tab calls `run_analysis` with the selected fields.
- **End to end:** the example questions against the real 20-float database through the Analyze tab
  and `run_analysis`; timings under 10 s; the chat with a real model once the user supplies an
  OpenRouter key in the app (Claude does not enter it).

## Out of scope

Model-written SQL; forecasts or derived oceanographic quantities (mixed-layer depth, density,
heat content); exporting chart data; polygons for ocean boundaries (boxes only).
