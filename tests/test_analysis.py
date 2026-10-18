import re
import unittest
from unittest.mock import patch

from analysis import AnalysisRequest, coverage_sentence, run_analysis
from database import QueryLimitError


class AnalysisRequestTests(unittest.TestCase):
    def test_rules_reject_impossible_combinations(self):
        cases = {
            'Profile counts use stat count.': dict(measure='profiles', stat='mean'),
            "Profile counts can't be drawn as that chart.": dict(measure='profiles', stat='count', chart='map'),
            'A scatter chart needs a different x_measure.': dict(chart='scatter'),
            'x_measure is only used by scatter charts.': dict(x_measure='psal'),
            'compare_values must list 1–4 values for compare_by.': dict(compare_by='region'),
            'Unknown comparison value.': dict(compare_by='region', compare_values=['atlantis']),
            'Put the regions in compare_values, not region.': dict(compare_by='region', compare_values=['equator'],
                                                                   region='arabian_sea'),
            'Comparing years needs group_by month_of_year or none.': dict(compare_by='year', compare_values=[2024],
                                                                         group_by='month'),
            'Comparisons work with line, bar and profile charts.': dict(compare_by='float', compare_values=['2902115'],
                                                                       chart='histogram'),
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
        self.assertEqual(AnalysisRequest(compare_by='year', compare_values=['2024'],
                                         group_by='month_of_year').compare_values, [2024])


class RunAnalysisTests(unittest.TestCase):
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

    def test_coverage_sentence(self):
        cov = dict(floats=20, profiles=3330, first_date='2013-11-24', last_date='2026-10-18',
                   lat=[-55.71447, 22.4463], lon=[11.129, 137.2407], measures={}, area='Indian Ocean')
        self.assertEqual(coverage_sentence(cov), 'This database has 20 floats in the Indian Ocean area '
                         '(55.7°S–22.4°N, 11.1°E–137.2°E), 2013-11-24 to 2026-10-18.')
        self.assertEqual(coverage_sentence(dict(cov, profiles=0)), 'The database has no profiles yet.')

    def test_too_many_points_becomes_a_status(self):
        with patch('analysis.fetch_rows', side_effect=QueryLimitError('Result exceeds 1000 rows')):
            result = run_analysis(group_by='day')
        self.assertEqual((result['status'], result['series']), ('too_many', []))


if __name__ == '__main__':
    unittest.main()
