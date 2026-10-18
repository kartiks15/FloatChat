import unittest
from datetime import datetime
from unittest.mock import patch
from dash import no_update
import app
from database import QueryLimitError


class DashboardTests(unittest.TestCase):
    POINT = dict(profile_id=9, wmo_id='2902115', latitude=0.5, longitude=80, date_utc='2024-03-15')
    SPEC = dict(variable='temp', label='Temperature', units='°C', group_by='month_of_year',
                pressure_range=[0, 10], bin_dbar=50, n_profiles=3, n_floats=1,
                first_date='2023-05-10', last_date='2024-04-15', filter_label='5°S–5°N · Mar–Jun',
                rows=[dict(bucket=3, mean=28, std=None, min=28, max=28, n_profiles=1, n_floats=1),
                      dict(bucket=4, mean=26, std=None, min=26, max=26, n_profiles=1, n_floats=1)])

    def test_starts_without_model_or_database(self):
        client = app.server.test_client()
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.get('/healthz').status_code, 200)
        self.assertEqual(client.get('/_dash-layout').status_code, 200)
        with patch('app.db_query', side_effect=RuntimeError('private credential')):
            response = client.get('/readyz')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b'credential', response.data)
            floats, _, status, _ = app.load_data('30')
            self.assertEqual(floats, {'error': 'database'})
            self.assertIn('Database unavailable', str(status))
            self.assertIn('is-error', str(status))
            self.assertIn('Database unavailable', str(app.render_float_list(floats, '', None)[0]))

    def test_date_filter_is_parameterized_and_has_one_outer_where(self):
        with patch('app.db_query', return_value=[]) as fetch:
            app.load_data('30')
            sql, params = fetch.call_args_list[0].args
            self.assertEqual(params, [30])
            self.assertIn("MAX(date_utc)", sql)
            self.assertEqual(sql.count('WHERE'), 1)
            self.assertNotIn('WHERE date_utc', sql)

    def test_invalid_date_filter_is_safe(self):
        with patch('app.db_query', return_value=[]) as fetch:
            app.load_data("30'; DROP TABLE floats;")
            self.assertEqual(fetch.call_args_list[0].args[1], [])

    def test_missing_temperature_not_faked_as_zero(self):
        profiles = [dict(latitude=12, longitude=72, wmo_id='2902115', date_utc='2020-01-01',
                         temp_max=None, profile_id=1)]
        fig = app.build_map(profiles)
        self.assertIn('unavailable', fig.data[0].text[0])
        self.assertEqual(list(fig.data[0].customdata[0]), [1, '2902115'])

    def test_separate_grids_and_bgc_units(self):
        rows = [dict(pres=10, temp=20, psal=35, doxy=150, chla=0.3, nitrate=2,
                     source_file=source, source_profile=0) for source in ['core.nc', 'bgc.nc']]
        fig = app.build_profile_chart(1, rows)
        self.assertEqual(len(fig.data), 4)
        self.assertEqual(len(fig.data[0].x), 1)
        bgc = app.build_bgc_chart(1, rows)
        self.assertEqual({trace.xaxis for trace in bgc.data}, {'x', 'x2', 'x3'})

    def test_bgc_chart_only_shows_measured_variables(self):
        rows = [dict(pres=10, doxy=None, chla=0.4, nitrate=None, bbp700=None, source_file='b.nc', source_profile=0)]
        fig = app.build_bgc_chart(1, rows)
        self.assertEqual(len(fig.data), 1)
        self.assertEqual([a.text for a in fig.layout.annotations], ['Chlorophyll-a (mg/m³)'])
        empty = app.build_bgc_chart(1, [dict(pres=10, source_file='c.nc', source_profile=0)])
        self.assertIn('No biogeochemical', empty.layout.annotations[0].text)

    def test_map_keeps_selection_trace_slots_and_frames_data(self):
        profiles = [dict(latitude=lat, longitude=lon, wmo_id='1', date_utc=None, temp_max=20, profile_id=i)
                    for i, (lat, lon) in enumerate([(10, 60), (20, 80)])]
        fig = app.build_map(profiles, trajectory=[], selected=None)
        self.assertEqual(len(fig.data), 5)
        # Centred on the data, nudged right so it sits in the gap between the side panels.
        self.assertGreater(fig.layout.map.center.lon, 70)
        self.assertLess(fig.layout.map.center.lon, 73)
        self.assertAlmostEqual(fig.layout.map.center.lat, 15)
        self.assertGreater(fig.layout.map.zoom, 2)

    def test_float_list_loading_and_empty_states(self):
        loading, _ = app.render_float_list(None, '', None)
        self.assertTrue(all(row.className == 'skeleton-row' for row in loading))
        self.assertIn('No floats yet', str(app.render_float_list([], '', None)[0]))

    def test_float_list_search_and_selection(self):
        floats = [dict(wmo_id='2902115', dac='IN', n_profiles=238, first='2013-11-24', last='2017-10-09'),
                  dict(wmo_id='5906539', dac='AO', n_profiles=145, first='2022-11-02', last='2026-10-12')]
        items, caption = app.render_float_list(floats, 'ao', '5906539')
        self.assertEqual(caption, '1 of 2 floats')
        self.assertIn('is-selected', items[0].className)

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

    def test_kpis_use_true_totals_not_the_float_list(self):
        with patch('app.db_query', return_value=[]), \
             patch('app.get_totals', return_value={'floats': 1234, 'profiles': 98765, 'latest': datetime(2026, 10, 12)}):
            kpis = app.load_data('all')[3]
        self.assertIn('1,234', str(kpis))
        self.assertIn('98,765', str(kpis))
        self.assertIn('2026-10-12', str(kpis))

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


if __name__ == '__main__':
    unittest.main()
