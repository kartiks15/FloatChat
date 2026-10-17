import unittest
from unittest.mock import patch
import app


class DashboardTests(unittest.TestCase):
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
        self.assertEqual(len(fig.data), 4)
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


if __name__ == '__main__':
    unittest.main()
