import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from ocean_tools import (RegionFilter, aggregate_measurements, describe_filter, execute_tool, filter_dict,
                         region_conditions, region_points)


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


if __name__ == '__main__':
    unittest.main()
