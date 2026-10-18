import json
import unittest
from unittest.mock import patch

import postgres_mcp


class McpTests(unittest.TestCase):
    def test_region_tools_forward_only_given_arguments(self):
        with patch('postgres_mcp.execute_tool', return_value={'n_profiles': 1}) as run:
            self.assertEqual(json.loads(postgres_mcp.find_region_profiles(region='equator', months=[3])), {'n_profiles': 1})
            run.assert_called_with('region_profiles', {'region': 'equator', 'months': [3]})
            postgres_mcp.analyze(region='pacific_ocean', stat='max')
            name, arguments = run.call_args.args
        self.assertEqual((name, arguments['stat'], arguments['region']), ('analyze', 'max', 'pacific_ocean'))
        self.assertNotIn('pres_max', arguments)


if __name__ == '__main__':
    unittest.main()
