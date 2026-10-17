"""Real PostgreSQL checks, enabled ONLY against an explicitly named disposable *_test DB.

CI supplies TEST_DATABASE_URL. These tests never use the developer's .env database.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import psycopg2
from psycopg2.extensions import parse_dsn

import argo_etl as etl
import database
import ocean_tools
from test_etl import make_fixture


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
