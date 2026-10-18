import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
from netCDF4 import Dataset
import argo_etl as etl
import download_argo


def make_fixture(path, temp=20, bad_pressure=False, *, latitude=12, longitude=179.9, juld=27000,
                 wmo='2902115', cycle=1, data_mode='D', extra=None):
    with Dataset(path, 'w') as nc:
        for name, size in [('N_PROF', 1), ('N_LEVELS', 3), ('STRING8', 8)]:
            nc.createDimension(name, size)
        nc.createVariable('PLATFORM_NUMBER', 'S1', ('N_PROF', 'STRING8'))[:] = np.array([list(wmo.ljust(8))], dtype='S1')
        for name, value in [('CYCLE_NUMBER', cycle), ('JULD', juld), ('LATITUDE', latitude), ('LONGITUDE', longitude)]:
            nc.createVariable(name, 'f8', ('N_PROF',), fill_value=99999)[:] = [value]
        for name, value in [('DIRECTION', b'A'), ('DATA_MODE', data_mode.encode())]:
            nc.createVariable(name, 'S1', ('N_PROF',))[:] = np.array([value], dtype='S1')
        for name, values in [('PRES', [10,20,30]), ('TEMP', [99,99,99]), ('TEMP_ADJUSTED', [temp,temp+1,temp+2])]:
            nc.createVariable(name, 'f8', ('N_PROF', 'N_LEVELS'), fill_value=99999)[:] = [values]
            flags = ['4','4','4'] if bad_pressure and name == 'PRES' else ['1','4','2']
            nc.createVariable(name+'_QC', 'S1', ('N_PROF', 'N_LEVELS'))[:] = np.array([flags], dtype='S1')
        for name, values in (extra or {}).items():   # e.g. {'PSAL': [35, 35.1, 35.2]}; 99999 is the fill value
            nc.createVariable(name, 'f8', ('N_PROF', 'N_LEVELS'), fill_value=99999)[:] = [values]
            nc.createVariable(name+'_QC', 'S1', ('N_PROF', 'N_LEVELS'))[:] = np.array([['1','1','1']], dtype='S1')


class EtlTests(unittest.TestCase):
    def test_mask_fill_and_bad_dates(self):
        for value in [np.ma.masked, float('nan'), float('inf'), 99999, None]:
            self.assertIsNone(etl.clean_float(value))
            self.assertIsNone(etl.juld_to_datetime(value))
        self.assertIsNone(etl.clean_qc(np.ma.masked))

    def test_delayed_adjusted_qc_and_actual_insert_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'D2902115_001.nc'
            make_fixture(path)
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchone.side_effect = [(1,), (None,)]
            with patch('argo_etl.execute_values') as insert:
                result = etl.process_file(conn, path)
            self.assertIsNone(result['error'])
            self.assertEqual(result['obs'], 2)
            rows = insert.call_args.args[2]
            self.assertEqual(rows[0][8], 20)
            self.assertEqual(rows[1][8], 22)
            self.assertEqual(rows[0][-3], 'C2902115_001.nc')
            conn.commit.assert_called_once()

    def test_file_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'D2902115_001.nc'
            make_fixture(path)
            conn = MagicMock()
            conn.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError('test')
            result = etl.process_file(conn, path)
            self.assertEqual(result['profiles'], 0)
            self.assertIsNotNone(result['error'])
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_refresh_summary_uses_concurrent_refresh_once_populated(self):
        for populated, statement in [(True, 'REFRESH MATERIALIZED VIEW CONCURRENTLY profile_summary'),
                                     (False, 'REFRESH MATERIALIZED VIEW profile_summary')]:
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = ('m', populated)
            self.assertTrue(etl.refresh_summary(conn))
            self.assertEqual(cur.execute.call_args.args[0], statement)
            conn.commit.assert_called_once()

    def test_refresh_summary_skips_unmigrated_schema(self):
        for row in [('v', True), None]:
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = row
            self.assertFalse(etl.refresh_summary(conn))
            self.assertEqual(cur.execute.call_count, 1)
            conn.commit.assert_not_called()

    def test_main_refreshes_summary_only_after_an_import(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ['D2902115_001.nc', 'D2902115_002.nc']:
                (Path(folder) / name).touch()
            argv = ['argo_etl.py', '--dir', folder]
            for errors, refreshed, exit_code in [([None, 'OSError'], True, 1),
                                                 (['OSError', 'OSError'], False, 1),
                                                 ([None, None], True, 0)]:
                results = [{'error': e, 'profiles': 0, 'obs': 0, 'skipped': 0} for e in errors]
                with patch('sys.argv', argv), patch('argo_etl.get_connection'), \
                        patch('argo_etl.process_file', side_effect=results), \
                        patch('argo_etl.refresh_summary', return_value=True) as refresh:
                    self.assertEqual(etl.main(), exit_code)
                self.assertEqual(refresh.called, refreshed)

    def test_main_pattern_selects_only_matching_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ['incois/2902115/profiles/D2902115_001.nc', 'aoml/5906539/5906539_Sprof.nc']:
                (Path(folder) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(folder) / name).touch()
            result = {'error': None, 'profiles': 1, 'obs': 2, 'skipped': 0}
            with patch('sys.argv', ['argo_etl.py', '--dir', folder, '--pattern', '*_Sprof.nc']), \
                    patch('argo_etl.get_connection'), patch('argo_etl.refresh_summary', return_value=True), \
                    patch('argo_etl.process_file', return_value=result) as process:
                self.assertEqual(etl.main(), 0)
            self.assertEqual([call.args[1].name for call in process.call_args_list], ['5906539_Sprof.nc'])

    def test_main_reports_failed_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'D2902115_001.nc').touch()
            result = {'error': None, 'profiles': 1, 'obs': 2, 'skipped': 0}
            with patch('sys.argv', ['argo_etl.py', '--dir', folder]), patch('argo_etl.get_connection') as connect, \
                    patch('argo_etl.process_file', return_value=result), \
                    patch('argo_etl.refresh_summary', side_effect=etl.psycopg2.OperationalError('lock')):
                self.assertEqual(etl.main(), 1)
            connect.return_value.rollback.assert_called_once()

    def test_source_key_keeps_distinct_grids(self):
        self.assertEqual(etl.source_key('R2902115_001.nc'), etl.source_key('D2902115_001.nc'))
        self.assertNotEqual(etl.source_key('BD2902115_001.nc'), etl.source_key('D2902115_001.nc'))

    def test_download_failure_does_not_leave_complete_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'file.nc'
            ftp = MagicMock()
            def fail(command, write):
                write(b'partial bytes')
                raise OSError('connection interrupted')
            ftp.retrbinary.side_effect = fail
            self.assertFalse(download_argo.download_file(ftp, '/file.nc', target))
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix('.nc.part').exists())
