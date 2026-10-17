import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
from netCDF4 import Dataset
import argo_etl as etl
import download_argo


def make_fixture(path, temp=20, bad_pressure=False):
    with Dataset(path, 'w') as nc:
        for name, size in [('N_PROF', 1), ('N_LEVELS', 3), ('STRING8', 8)]:
            nc.createDimension(name, size)
        nc.createVariable('PLATFORM_NUMBER', 'S1', ('N_PROF', 'STRING8'))[:] = np.array([list('2902115 ')], dtype='S1')
        for name, value in [('CYCLE_NUMBER', 1), ('JULD', 27000), ('LATITUDE', 12), ('LONGITUDE', 179.9)]:
            nc.createVariable(name, 'f8', ('N_PROF',), fill_value=99999)[:] = [value]
        for name, value in [('DIRECTION', b'A'), ('DATA_MODE', b'D')]:
            nc.createVariable(name, 'S1', ('N_PROF',))[:] = np.array([value], dtype='S1')
        for name, values in [('PRES', [10,20,30]), ('TEMP', [99,99,99]), ('TEMP_ADJUSTED', [temp,temp+1,temp+2])]:
            nc.createVariable(name, 'f8', ('N_PROF', 'N_LEVELS'), fill_value=99999)[:] = [values]
            flags = ['4','4','4'] if bad_pressure and name == 'PRES' else ['1','4','2']
            nc.createVariable(name+'_QC', 'S1', ('N_PROF', 'N_LEVELS'))[:] = np.array([flags], dtype='S1')


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
