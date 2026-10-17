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
            self.assertEqual(app.load_data('30')[2], 'DATABASE UNAVAILABLE')

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


if __name__ == '__main__':
    unittest.main()
