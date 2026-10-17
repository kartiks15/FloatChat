import unittest
from unittest.mock import patch, MagicMock
import database


class DatabaseTests(unittest.TestCase):
    def test_bounded_fetch_and_connection_closed(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchmany.return_value = [{'x': 1}, {'x': 2}]
        with patch('database.connect_readonly', return_value=conn):
            with self.assertRaises(database.QueryLimitError):
                database.fetch_rows('SELECT 1', max_rows=1)
        conn.close.assert_called_once()

    def test_readonly_and_timeout(self):
        with patch('database.get_db_config', return_value={}), patch('database.psycopg2.connect') as connect:
            conn = database.connect_readonly()
            self.assertIn('statement_timeout=', connect.call_args.kwargs['options'])
            self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
            conn.set_session.assert_called_once_with(readonly=True, autocommit=False)
