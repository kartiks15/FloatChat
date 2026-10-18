"""Real PostgreSQL checks, enabled ONLY against an explicitly named disposable *_test DB.

CI supplies TEST_DATABASE_URL. These tests never use the developer's .env database.
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import psycopg2
from psycopg2.extensions import parse_dsn

import analysis
import argo_etl as etl
import database
import ocean_tools
from test_etl import make_fixture


def juld(day):  # days since 1950-01-01, as stored in Argo files
    return (datetime.fromisoformat(day) - datetime(1950, 1, 1)).days


EQUATOR_FIXTURES = [  # (cycle, lat, lon, date, temp)
    (1, 0.5, 80, '2024-03-15', 28),   # A: in band, March 2024
    (2, -1.0, 85, '2024-04-15', 26),  # B: in band, April 2024
    (3, 12.0, 72, '2024-03-20', 30),  # C: Arabian Sea, outside band
    (4, 1.0, 82, '2023-05-10', 27),   # D: in band, May of another year
    (5, 0.0, 80, '2024-08-01', 25),   # E: in band, outside March–June
]


def load_fixtures(conn, folder, fixtures=EQUATOR_FIXTURES, prefix='D'):
    for cycle, lat, lon, day, temp in fixtures:
        path = Path(folder) / f'{prefix}2902115_{cycle:03d}.nc'
        make_fixture(path, temp, latitude=lat, longitude=lon, juld=juld(day), cycle=cycle)
        assert etl.process_file(conn, path)['error'] is None
    assert etl.refresh_summary(conn)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL to a disposable PostgreSQL *_test database')
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = parse_dsn(os.environ['TEST_DATABASE_URL'])
        if not cls.config.get('dbname', '').endswith('_test'):
            raise RuntimeError('Refusing integration tests: database name must end in _test')
        cls.conn = psycopg2.connect(**cls.config)
        etl.apply_schema(cls.conn, Path(__file__).resolve().parents[1] / 'schema.sql')
        cls.config_patch = patch('database.get_db_config', return_value=cls.config)
        cls.config_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.config_patch.stop()
        cls.conn.close()

    def setUp(self):
        analysis._coverage.cache_clear()
        with self.conn.cursor() as cur:
            cur.execute('TRUNCATE observations, profiles, floats RESTART IDENTITY CASCADE')
        self.conn.commit()
        self.assertTrue(etl.refresh_summary(self.conn))

    def relkind(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT relkind FROM pg_class WHERE oid = to_regclass('profile_summary')")
            kind = cur.fetchone()[0]
        self.conn.commit()
        return kind

    def test_repeat_import_preserves_core_and_bgc_and_schema_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Path(folder) / 'D2902115_001.nc'
            bgc = Path(folder) / 'BD2902115_001.nc'
            make_fixture(core)
            make_fixture(bgc)
            for path in [core, bgc, core]:
                result = etl.process_file(self.conn, path)
                self.assertIsNone(result['error'])
                self.assertEqual(result['obs'], 2)
            etl.apply_schema(self.conn, Path(__file__).resolve().parents[1] / 'schema.sql')
            self.assertIsNone(ocean_tools.profile_data(1)['profile'])  # summary is stale until refreshed
            self.assertTrue(etl.refresh_summary(self.conn))
            totals = ocean_tools.dataset_summary()
            self.assertEqual(totals['profiles'], 1)
            self.assertEqual(totals['observations'], 4)
            profile = ocean_tools.profile_data(1)
            self.assertEqual(profile['profile']['n_levels'], 4)
            self.assertEqual(len(profile['rows']), 4)
            self.assertEqual({row['temp'] for row in profile['rows']}, {20, 22})

    def test_schema_replaces_legacy_plain_view_with_materialized_summary(self):
        with self.conn.cursor() as cur:
            cur.execute('DROP MATERIALIZED VIEW profile_summary')
            cur.execute('CREATE VIEW profile_summary AS SELECT profile_id FROM profiles')
        self.conn.commit()
        self.assertEqual(self.relkind(), 'v')
        self.assertFalse(etl.refresh_summary(self.conn))
        etl.apply_schema(self.conn, Path(__file__).resolve().parents[1] / 'schema.sql')
        self.assertEqual(self.relkind(), 'm')
        etl.apply_schema(self.conn, Path(__file__).resolve().parents[1] / 'schema.sql')
        self.assertTrue(etl.refresh_summary(self.conn))

    def test_geographic_search_crosses_antimeridian_and_end_date_is_inclusive(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Path(folder) / 'D2902115_001.nc'
            make_fixture(core)
            self.assertIsNone(etl.process_file(self.conn, core)['error'])
            self.assertTrue(etl.refresh_summary(self.conn))
            day = etl.juld_to_datetime(27000).date().isoformat()
            result = ocean_tools.search_profiles(latitude=12, longitude=-179.9, radius_km=50,
                                                 start_date=day, end_date=day)
            self.assertEqual(len(result['rows']), 1)
            self.assertLess(result['rows'][0]['distance_km'], 25)
            self.assertEqual(ocean_tools.search_profiles(latitude=12, longitude=-179.9, radius_km=1)['rows'], [])

    def test_region_profiles_equator_march_to_june(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = ocean_tools.region_profiles(region='equator', months=[3, 4, 5, 6])
        self.assertEqual((result['n_profiles'], result['n_floats']), (3, 1))
        self.assertEqual((result['first_date'], result['last_date']), ('2023-05-10', '2024-04-15'))
        self.assertEqual(len(ocean_tools.region_points(region='equator')['rows']), 4)

    def test_region_profiles_across_antimeridian(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder, [(1, 0, -179.5, '2024-03-01', 28), (2, 0, 100, '2024-03-01', 28)])
        result = ocean_tools.region_profiles(lat_min=-5, lat_max=5, lon_min=170, lon_max=-170)
        self.assertEqual(result['n_profiles'], 1)

    def test_reimport_upgrades_profile_data_mode_but_never_downgrades(self):
        with tempfile.TemporaryDirectory() as folder:
            real, delayed = Path(folder) / 'R2902115_001.nc', Path(folder) / 'D2902115_001.nc'
            make_fixture(real, data_mode='R')
            make_fixture(delayed)
            for path, expected in [(real, ('R', 'R2902115_001.nc')), (delayed, ('D', 'D2902115_001.nc')),
                                   (real, ('D', 'D2902115_001.nc'))]:
                self.assertIsNone(etl.process_file(self.conn, path)['error'])
                with self.conn.cursor() as cur:
                    cur.execute('SELECT data_mode, source_file FROM profiles')
                    self.assertEqual(cur.fetchone(), expected)
                self.conn.commit()

    def test_analysis_mean_by_calendar_month_across_years(self):
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

    def test_histogram_scatter_and_map(self):
        with tempfile.TemporaryDirectory() as folder:
            for cycle, lat, lon, day, temp in EQUATOR_FIXTURES:
                path = Path(folder) / f'D2902115_{cycle:03d}.nc'
                make_fixture(path, temp, latitude=lat, longitude=lon, juld=juld(day), cycle=cycle,
                             extra={'PSAL': [35.0, 35.1, 35.2]})
                self.assertIsNone(etl.process_file(self.conn, path)['error'])
            self.assertTrue(etl.refresh_summary(self.conn))
        hist = analysis.run_analysis(region='equator', chart='histogram', bins=5)
        self.assertEqual((sum(p['y'] for p in hist['points']), hist['chart']), (4, 'histogram'))
        scatter = analysis.run_analysis(region='equator', chart='scatter', measure='temp', x_measure='psal')
        self.assertEqual(len(scatter['points']), 8)                      # 4 profiles x 2 kept levels
        self.assertEqual({p['x'] for p in scatter['points']}, {35.0, 35.2})
        points = analysis.run_analysis(region='equator', chart='map')['points']
        self.assertEqual(sorted(p['value'] for p in points), [25, 26, 27, 28])

    def test_pacific_has_no_data_and_says_why(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        result = analysis.run_analysis(region='pacific_ocean')
        self.assertEqual(result['status'], 'no_data')
        self.assertTrue(result['diagnosis'].startswith(
            'No profiles in the Pacific Ocean (approximate box) at all. This database has 1 float'))

    def test_dates_and_months_diagnoses(self):
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder)
        dated = analysis.run_analysis(region='equator', start_date='2010-01-01', end_date='2010-12-31')
        self.assertIn('The equatorial band (5°S–5°N) has 4 profiles, but none between 2010-01-01 and 2010-12-31.',
                      dated['diagnosis'])
        self.assertIn('but none in Sep.', analysis.run_analysis(region='equator', months=[9])['diagnosis'])
        self.assertIn('Float 1234567 has no profiles in',
                      analysis.run_analysis(region='equator', wmo_id='1234567')['diagnosis'])

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

    def test_ocean_boxes_follow_australia(self):
        # Water south of Australia west of Tasmania (~147°E) is Indian Ocean, not Pacific.
        with tempfile.TemporaryDirectory() as folder:
            load_fixtures(self.conn, folder, [(1, -55.0, 136.0, '2024-03-01', 10), (2, 15.0, 130.0, '2024-03-01', 29)])
        pacific = analysis.run_analysis(region='pacific_ocean', group_by='none')
        self.assertEqual(pacific['series'][0]['points'][0]['y'], 29)       # only the Philippine Sea profile
        indian = analysis.run_analysis(region='indian_ocean', group_by='none')
        self.assertEqual(indian['series'][0]['points'][0]['y'], 10)        # only the profile south of Australia

    def test_readonly_connection_rejects_writes_even_for_admin(self):
        conn = database.connect_readonly()
        try:
            with conn.cursor() as cur:
                with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                    cur.execute("INSERT INTO floats(wmo_id) VALUES('1234567')")
        finally:
            conn.close()

    def test_database_error_rolls_back_whole_file(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Path(folder) / 'D2902115_001.nc'
            make_fixture(core)
            with patch('argo_etl.execute_values', side_effect=RuntimeError('simulated interruption')):
                result = etl.process_file(self.conn, core)
            self.assertIsNotNone(result['error'])
            self.assertEqual(ocean_tools.dataset_summary()['profiles'], 0)
