import unittest

import charts


def series(name, points):
    return dict(name=name, points=points, n_profiles=sum(p['n_profiles'] for p in points), n_floats=1,
                first_date='2024-03-01', last_date='2024-06-30')


BASE = dict(status='ok', title='Mean temperature by calendar month · Equator', measure='temp',
            measure_label='Temperature', units='°C', stat='mean', group_by='month_of_year', chart='line',
            pressure_range=[0, 10], depth_bin=50, x_measure=None, notes=[], diagnosis=None, n_profiles=6,
            n_floats=1, filter={'region': 'equator'}, filter_label='Equator', points=[])


def P(x, y, n=3):
    return dict(x=x, y=y, n_profiles=n, n_floats=1, low=n < 3)


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
        sc = charts.build_chart(dict(BASE, chart='scatter', x_measure='psal', series=[],
                                     points=[dict(x=35, y=28, pres=10)]))
        self.assertEqual(sc.data[0].mode, 'markers')
        mp = charts.build_chart(dict(BASE, chart='map', series=[],
                                     points=[dict(lat=0, lon=80, value=28, profile_id=1, wmo_id='1')]))
        self.assertEqual(mp.data[0].type, 'scattergeo')   # no tile style to load, unlike scattermap

    def test_messages_instead_of_charts(self):
        no_data = dict(BASE, status='no_data', series=[],
                       diagnosis='No profiles in the Pacific Ocean (approximate box) at all.')
        self.assertEqual(charts.describe_result(no_data)['message'], no_data['diagnosis'])
        self.assertIn('Too many points', charts.describe_result(dict(BASE, status='too_many', series=[]))['message'])
        caption = charts.describe_result(dict(BASE, series=[series('E', [P(3, 28), P(4, 26)])]))['caption']
        self.assertIn("Average of each profile's mean, 0–10 dbar · 6 profiles from 1 float · 2024-03-01 to 2024-06-30",
                      caption)


if __name__ == '__main__':
    unittest.main()
