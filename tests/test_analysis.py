import re
import unittest

from analysis import AnalysisRequest


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


if __name__ == '__main__':
    unittest.main()
