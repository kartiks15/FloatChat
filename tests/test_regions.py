import json
import unittest
from datetime import date

from ocean_tools import RegionFilter, describe_filter, filter_dict, region_conditions


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


if __name__ == '__main__':
    unittest.main()
