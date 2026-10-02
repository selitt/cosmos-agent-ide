import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        with patch('server.find_codex', return_value='fake-codex'):
            self.ws = server.Workspace(self.root)

    def tearDown(self):
        for job in self.ws.jobs.values():
            job.stop()
        self.temp.cleanup()

    def wait(self, job_id):
        job = self.ws.jobs[job_id]
        deadline = time.monotonic() + 8
        while job.status == 'running' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertNotEqual(job.status, 'running')
        return job

    def test_escape_and_symlink(self):
        with self.assertRaises(ValueError): server.safe_path(self.root, '../outside.py')
        (self.root / 'link').symlink_to(self.root.parent)
        with self.assertRaises(ValueError): server.safe_path(self.root, 'link/outside.py')

    def test_save_conflict_and_new_file(self):
        result = self.ws.save({'path': 'a.py', 'content': 'print(1)', 'revision': None})
        with self.assertRaises(ValueError):
            self.ws.save({'path': 'a.py', 'content': 'oops', 'revision': None})
        (self.root / 'a.py').write_text('external edit')
        with self.assertRaises(ValueError):
            self.ws.save({'path': 'a.py', 'content': 'oops', 'revision': result['revision']})
        self.assertEqual((self.root / 'a.py').read_text(), 'external edit')

    def test_roundtrip_and_binary(self):
        self.ws.save({'path': 'folder/привет.py', 'content': '# Привет\n', 'revision': None})
        self.assertEqual(self.ws.read('folder/привет.py')['content'], '# Привет\n')
        (self.root / 'bad.bin').write_bytes(b'\x00\xff')
        with self.assertRaises(ValueError): self.ws.read('bad.bin')

    def test_team_validation(self):
        nodes = self.ws.team()
        self.ws.validate_team(nodes)
        nodes[0]['parent'] = 'developer'
        with self.assertRaises(ValueError): self.ws.validate_team(nodes)
        nodes = self.ws.team()
        nodes[2]['parent'] = 'qa'; nodes[3]['parent'] = 'developer'
        with self.assertRaises(ValueError): self.ws.validate_team(nodes)

    def test_team_bottom_up_and_parallel(self):
        nodes = self.ws.team()
        for node in nodes: node['provider'] = 'gemini'
        active = 0; maximum = 0; lock = threading.Lock(); prompts = []
        def fake(key, model, messages):
            nonlocal active, maximum
            prompt = messages[0]['content']
            with lock:
                active += 1; maximum = max(maximum, active); prompts.append(prompt)
            time.sleep(.08)
            with lock: active -= 1
            return 'REPORT_OK'
        with patch('server.gemini_request', side_effect=fake):
            job = self.wait(self.ws.run_team({'nodes': nodes, 'task': 'Build CSV app', 'key': 'test-key'})['id'])
        self.assertEqual(job.status, 'done')
        self.assertEqual(len(job.agents), 4)
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 3)
        self.assertIn('REPORT_OK', prompts[-1])
        self.assertTrue(all(v['status'] == 'done' for v in job.agents.values()))

    def test_team_partial_failure_is_visible(self):
        nodes = self.ws.team()[:2]
        for node in nodes: node['provider'] = 'gemini'
        with patch('server.gemini_request', side_effect=[ValueError('quota'), 'Summary with missing report']):
            job = self.wait(self.ws.run_team({'nodes': nodes, 'task': 'Test', 'key': 'test'})['id'])
        self.assertEqual(job.status, 'error')
        self.assertEqual(job.agents['architect']['status'], 'error')
        self.assertIn('Summary', job.answer)

    def test_python_execution(self):
        path = self.root / 'hello.py'; path.write_text('print("HELLO_PYTHON")')
        job = self.wait(self.ws.launch('run', lambda j: self.ws.process(j, [sys.executable, '-u', str(path)]))['id'])
        self.assertEqual(job.code, 0); self.assertIn('HELLO_PYTHON', job.output)

    def test_process_cancel(self):
        path = self.root / 'wait.py'; path.write_text('import time\ntime.sleep(60)')
        result = self.ws.launch('run', lambda j: self.ws.process(j, [sys.executable, str(path)]))
        self.ws.jobs[result['id']].stop()
        self.assertEqual(self.wait(result['id']).status, 'cancelled')

    def test_no_gemini_key_reaches_script(self):
        path = self.root / 'env.py'; path.write_text('import os\nprint(os.getenv("GEMINI_API_KEY", "ABSENT"))')
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'SECRET_EXAMPLE'}):
            job = self.wait(self.ws.launch('run', lambda j: self.ws.process(j, [sys.executable, str(path)]))['id'])
        self.assertIn('ABSENT', job.output); self.assertNotIn('SECRET_EXAMPLE', job.output)

    def test_codex_event_parsing(self):
        path = self.root / 'events.py'
        path.write_text('import json\nprint(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"CODEX_OK"}}))')
        job = self.wait(self.ws.launch('chat', lambda j: self.ws.process(j, [sys.executable, str(path)], codex=True))['id'])
        self.assertEqual(job.answer.strip(), 'CODEX_OK')


class GeminiTests(unittest.TestCase):
    def test_request_format_and_response(self):
        result = {'candidates': [{'content': {'parts': [{'text': 'hidden', 'thought': True}, {'text': 'GEMINI_OK'}]}}]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(result).encode())) as opened:
            self.assertEqual(server.gemini_request('TEST_KEY', 'gemini-3.8-flash', [{'role':'user','content':'Hi'}]), 'GEMINI_OK')
        request = opened.call_args.args[0]
        self.assertNotIn('TEST_KEY', request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), 'TEST_KEY')
        self.assertEqual(json.loads(request.data)['contents'][0]['role'], 'user')

    def test_error_never_echoes_key(self):
        error = urllib.error.HTTPError('url', 403, 'SECRET_KEY', {}, io.BytesIO(b'SECRET_KEY'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(ValueError) as raised:
                server.gemini_request('SECRET_KEY', 'gemini-3.8-flash', [])
        self.assertNotIn('SECRET_KEY', str(raised.exception))
        self.assertIn('403', str(raised.exception))


class HttpTests(unittest.TestCase):
    def test_auth_origin_and_save(self):
        with tempfile.TemporaryDirectory() as temp, patch('server.find_codex', return_value=None):
            ws = server.Workspace(Path(temp))
            http = server.ThreadingHTTPServer(('127.0.0.1',0),server.handler_for(ws))
            thread = threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base = f'http://127.0.0.1:{http.server_port}'
            try:
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(base+'/api/info')
                self.assertEqual(error.exception.code,403)
                request = urllib.request.Request(base+'/api/info',headers={'X-IDE-Token':ws.token,'Origin':'https://evil.example'})
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,403)
                request = urllib.request.Request(base+'/api/save',data=json.dumps({'path':'test.py','content':'print(123)','revision':None}).encode(),headers={'X-IDE-Token':ws.token,'Content-Type':'application/json'})
                with urllib.request.urlopen(request) as response: self.assertEqual(response.status,200)
                self.assertEqual((Path(temp)/'test.py').read_text(),'print(123)')
            finally:
                http.shutdown();http.server_close();thread.join()


if __name__ == '__main__': unittest.main()
