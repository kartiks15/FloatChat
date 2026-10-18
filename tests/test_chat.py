import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, MagicMock

import chat_service as chat
from ocean_tools import execute_tool, SearchArgs


class ChatTests(unittest.TestCase):
    def test_disabled_chat_never_calls_provider(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'false'}), patch('chat_service.generate') as generate:
            response, selection = chat.answer_question('List floats')
            self.assertIn('disabled', response)
            generate.assert_not_called()
            self.assertIsNone(selection)

    def test_daily_quota_is_atomic_across_connections(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'AI_QUOTA_DB': str(Path(folder) / 'quota.db'), 'AI_DAILY_REQUEST_LIMIT': '3',
            'AI_HOURLY_CLIENT_LIMIT': '100'}):
            def attempt(_):
                try:
                    chat.reserve_request('same-client')
                    return True
                except chat.QuotaExceeded:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(attempt, range(12))), 3)
            with self.assertRaises(chat.QuotaExceeded):
                chat.reserve_request('different-client')

    def test_plan_rejects_raw_sql_and_unknown_arguments(self):
        with self.assertRaises(ValueError):
            chat.parse_plan('{"tool":"query_data","arguments":{"sql":"DELETE FROM floats"}}')
        with self.assertRaises(ValueError):
            execute_tool('dataset_summary', {'sql': 'SELECT 1'})
        with self.assertRaises(ValueError):
            SearchArgs(latitude=91, longitude=0)
        with self.assertRaises(ValueError):
            SearchArgs(start_date='2024-02-02', end_date='2024-01-01')
        with self.assertRaises(ValueError):
            SearchArgs(latitude=20)

    def test_grounded_answer_and_selection(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.generate', side_effect=['{"tool":"profile_data","arguments":{"profile_id":1}}', 'Temperature is 20 C.']) as model, \
             patch('chat_service.execute_tool', return_value={'profile': {'profile_id': 1, 'wmo_id': '2902115'}, 'rows': [{'temp': 20}]}):
            answer, actions = chat.answer_question('Show profile 1')
            self.assertEqual(model.call_count, 2)
            self.assertIn('20', model.call_args.args[1])
            self.assertEqual(actions['select']['profile_id'], 1)
            self.assertIn('20', answer)

    def test_limits_and_redacts_errors(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request') as reserve:
            chat.answer_question('x' * 1001)
            reserve.assert_not_called()
            with patch('chat_service.generate', side_effect=RuntimeError('secret-password')):
                self.assertNotIn('secret-password', chat.answer_question('List floats')[0])

    def test_context_is_valid_bounded_json_with_sampling_notice(self):
        result = json.loads(chat.bounded_context({'rows': [{'text': 'x' * 500} for _ in range(100)], 'truncated': False}))
        self.assertTrue(result['context_sampled'])
        self.assertEqual(result['returned_rows'], 100)

    def test_ollama_request_uses_native_api_and_no_openai_key(self):
        response = MagicMock()
        response.iter_content.return_value = [b'{"message":{"content":"hello"}}']
        response.__enter__.return_value = response
        with patch.dict(os.environ, {'AI_PROVIDER': 'ollama'}), patch('chat_service.requests.post', return_value=response) as post:
            self.assertEqual(chat.generate('system', 'question'), 'hello')
            self.assertTrue(post.call_args.args[0].endswith('/api/chat'))
            self.assertFalse(post.call_args.kwargs['json']['stream'])

    def test_visitor_openrouter_key_is_used_without_server_ai(self):
        key = 'sk-or-v1-' + 'a' * 64
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b'{"choices":[{"message":{"content":"hello"}}]}']
        response.__enter__.return_value = response
        with patch.dict(os.environ, {'AI_ENABLED': 'false', 'AI_PROVIDER': 'ollama'}), \
             patch('chat_service.requests.post', return_value=response) as post:
            self.assertEqual(chat.generate('system', 'question', json_mode=True, api_key=key,
                                           model='https://evil.example/model'), 'hello')
            self.assertEqual(post.call_args.args[0], chat.OPENROUTER_URL)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer ' + key)
            body = post.call_args.kwargs['json']
            self.assertEqual(body['model'], chat.OPENROUTER_DEFAULT_MODEL)
            self.assertEqual(body['response_format'], {'type': 'json_object'})

    def test_visitor_key_skips_shared_quota_and_disabled_server(self):
        key = 'sk-or-v1-' + 'b' * 64
        with patch.dict(os.environ, {'AI_ENABLED': 'false'}), \
             patch('chat_service.reserve_request') as reserve, \
             patch('chat_service.generate', side_effect=['{"tool":"dataset_summary","arguments":{}}', 'Two floats.']) as model, \
             patch('chat_service.execute_tool', return_value={'floats': 2}):
            answer, _ = chat.answer_question('Summarise', api_key=key, model='meta-llama/llama-3.3-70b-instruct')
            self.assertEqual(answer, 'Two floats.')
            self.assertFalse(reserve.call_args.kwargs['shared'])
            self.assertEqual(model.call_args.kwargs['api_key'], key)

    def test_invalid_or_disallowed_visitor_key(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'false'}), patch('chat_service.generate') as generate:
            self.assertIn('sk-or-', chat.answer_question('List floats', api_key='not-a-key\r\nX: y')[0])
            with patch.dict(os.environ, {'ALLOW_USER_API_KEYS': 'false'}):
                self.assertIn('disabled', chat.answer_question('List floats', api_key='sk-or-v1-' + 'c' * 64)[0])
            generate.assert_not_called()

    def test_rejected_key_message_never_echoes_key(self):
        key = 'sk-or-v1-' + 'd' * 64
        response = MagicMock(status_code=401)
        response.__enter__.return_value = response
        with patch.dict(os.environ, {'AI_ENABLED': 'false'}), patch('chat_service.reserve_request'), \
             patch('chat_service.requests.post', return_value=response):
            answer, _ = chat.answer_question('List floats', api_key=key)
            self.assertIn('rejected', answer)
            self.assertNotIn(key, answer)

    def test_own_key_quota_is_separate_and_hourly(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'AI_QUOTA_DB': str(Path(folder) / 'quota.db'), 'AI_DAILY_REQUEST_LIMIT': '0',
            'AI_USER_KEY_HOURLY_LIMIT': '2'}):
            chat.reserve_request('visitor', shared=False)
            chat.reserve_request('visitor', shared=False)
            with self.assertRaises(chat.QuotaExceeded):
                chat.reserve_request('visitor', shared=False)
            with self.assertRaises(chat.QuotaExceeded):
                chat.reserve_request('visitor')

    def test_dashboard_actions_for_each_tool(self):
        plan = lambda tool: chat.ToolPlan(tool=tool, arguments={})
        region = {'n_profiles': 3, 'filter': {'region': 'equator'}, 'label': '5°S–5°N'}
        aggregate = {'status': 'ok', 'n_profiles': 3, 'filter': {'region': 'equator'}, 'filter_label': '5°S–5°N', 'series': []}
        self.assertEqual(chat.dashboard_actions(plan('region_profiles'), region),
                         {'highlight': {'filter': {'region': 'equator'}, 'label': '5°S–5°N'}})
        actions = chat.dashboard_actions(plan('analyze'), aggregate)
        self.assertEqual((actions['chart'], actions['highlight']['label']), (aggregate, '5°S–5°N'))
        self.assertEqual(chat.dashboard_actions(plan('trajectory'), {'rows': [{'profile_id': 7, 'wmo_id': '1'}]}),
                         {'select': {'profile_id': 7, 'wmo_id': '1'}})
        self.assertIsNone(chat.dashboard_actions(plan('dataset_summary'), {'floats': 2}))

    def test_empty_region_result_clears_the_old_highlight(self):
        # A stale highlight of an earlier region next to "no measurements" would mislead.
        plan = chat.ToolPlan(tool='analyze', arguments={})
        empty = {'status': 'no_data', 'n_profiles': 0, 'filter': {}, 'filter_label': 'All profiles', 'series': []}
        self.assertEqual(chat.dashboard_actions(plan, empty), {'chart': empty, 'highlight': None})
        self.assertEqual(chat.dashboard_actions(chat.ToolPlan(tool='region_profiles', arguments={}),
                                                {'n_profiles': 0, 'filter': {}, 'label': 'x'}), {'highlight': None})

    def test_prompts_describe_regions_and_dashboard(self):
        with patch.dict(os.environ, {'AI_ENABLED': 'true'}), patch('chat_service.reserve_request'), \
             patch('chat_service.generate', side_effect=['{"tool":"region_profiles","arguments":{"region":"equator"}}', 'Three.']) as model, \
             patch('chat_service.execute_tool', return_value={'n_profiles': 3, 'filter': {'region': 'equator'}, 'label': '5°S–5°N'}):
            answer, actions = chat.answer_question('Highlight floats on the equator')
        plan_prompt, answer_prompt = model.call_args_list[0].args[0], model.call_args_list[1].args[0]
        self.assertIn('March to June', plan_prompt)
        self.assertIn('5°S–5°N', plan_prompt)
        self.assertNotIn('claim a map was changed', answer_prompt)
        self.assertIn('dashboard', answer_prompt)
        self.assertEqual(actions['highlight']['label'], '5°S–5°N')

    def test_analyze_and_unsupported_actions(self):
        plan = lambda tool: chat.ToolPlan(tool=tool, arguments={})
        result = {'status': 'ok', 'n_profiles': 3, 'filter': {'region': 'equator'}, 'filter_label': 'Equator', 'series': []}
        self.assertEqual(chat.dashboard_actions(plan('analyze'), result),
                         {'chart': result, 'highlight': {'filter': {'region': 'equator'}, 'label': 'Equator'}})
        compared = dict(result, filter=None)
        self.assertIsNone(chat.dashboard_actions(plan('analyze'), compared)['highlight'])
        refused = execute_tool('unsupported', {'reason': 'Mixed-layer depth is not available.'})
        self.assertEqual(chat.dashboard_actions(plan('unsupported'), refused), {'chart': refused})
        self.assertEqual(refused['status'], 'unsupported')

    def test_bedrock_converse_request(self):
        client = MagicMock()
        client.converse.return_value = {'output': {'message': {'content': [{'text': 'answer'}]}}}
        with patch.dict(os.environ, {'AI_PROVIDER': 'bedrock'}), patch('boto3.client', return_value=client):
            self.assertEqual(chat.generate('system', 'question'), 'answer')
            self.assertEqual(client.converse.call_args.kwargs['modelId'], 'qwen.qwen3-32b-v1:0')
