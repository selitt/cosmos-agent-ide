import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Workspace
from ide_tools import complete

class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        with patch('server.find_codex', return_value=None), patch('server.find_gemini', return_value=None), patch('server.find_agy', return_value=None):
            self.ws = Workspace(self.root)
        self.ws.python = sys.executable
    def tearDown(self):
        for job in self.ws.jobs.values(): job.stop()
        for job in self.ws.jobs.values():
            if hasattr(job, 'proc'):
                try: job.proc.wait(timeout=3)
                except Exception: pass
        self.tmp.cleanup()
    def wait(self, job, condition):
        end = time.monotonic() + 7
        while time.monotonic() < end:
            result = job.snapshot()
            if condition(result): return result
            time.sleep(.02)
        self.fail('Timeout: ' + repr(job.snapshot()))
    def start(self, kind, text, **extra):
        (self.root / 'sample.py').write_text(text)
        ident = self.ws.start_session(kind, {'path':'sample.py','python':sys.executable, **extra})['id']
        return self.ws.jobs[ident]
    def test_run_accepts_input_and_arguments(self):
        job = self.start('run', "import sys\nname=input('Name: ')\nprint(name,sys.argv[1])\n", args='"two words"')
        self.wait(job, lambda s:'Name:' in s['output'])
        job.send('Cosmos\n')
        result = self.wait(job, lambda s:s['status'] != 'running')
        self.assertEqual(result['code'], 0)
        self.assertIn('Cosmos two words', result['output'])
    def test_terminal_has_project_cwd_and_preserves_session(self):
        ident = self.ws.start_session('terminal', {})['id']; job=self.ws.jobs[ident]
        job.send("export COSMOS_TEST_VALUE=ok\npwd\nprintf 'RESULT_%s\\n' \"$COSMOS_TEST_VALUE\"\n")
        result=self.wait(job, lambda s:'RESULT_ok' in s['output'])
        self.assertIn(str(self.root), result['output'])
        self.assertEqual(result['status'], 'running')
        job.send('exit\n');self.wait(job, lambda s:s['status']!='running')
    def test_terminal_interrupt_stops_foreground_program(self):
        ident=self.ws.start_session('terminal', {})['id'];job=self.ws.jobs[ident]
        job.send(f"{sys.executable} -u -c \"import time; print('READY'); time.sleep(60)\"\n")
        self.wait(job,lambda s:'READY\r\n' in s['output'])
        job.send('\x03');job.send("printf 'INTERRUPTED_OK\\n'\n")
        self.wait(job,lambda s:'INTERRUPTED_OK\r\n' in s['output'])
    def test_debug_breakpoint_and_next_inspect_locals(self):
        job=self.start('debug', 'x=2\nx+=3\nprint(x)\n', breakpoints={'sample.py':[3]})
        first=self.wait(job, lambda s:s['debug']['state']=='paused')
        self.assertEqual(first['debug']['line'],1)
        job.send({'action':'continue'})
        stopped=self.wait(job, lambda s:s['debug'].get('line')==3 and s['debug']['sequence']>first['debug']['sequence'])
        self.assertEqual(stopped['debug']['locals']['x'],'5')
        self.assertEqual(stopped['debug']['frames'][0]['path'],'sample.py')
        job.send({'action':'next'})
        result=self.wait(job, lambda s:s['status']!='running')
        self.assertEqual(result['code'],0)
        self.assertIn('5',result['output'])
    def test_debug_input_and_exception(self):
        job=self.start('debug', "name=input('Name? ')\nprint(name)\nraise ValueError('boom')\n")
        self.wait(job,lambda s:s['debug']['state']=='paused');job.send({'action':'continue'})
        self.wait(job,lambda s:s['debug']['state']=='input');job.send({'action':'input','text':'Ada'})
        result=self.wait(job,lambda s:s['debug'].get('reason')=='exception')
        self.assertIn('Ada',result['output']);self.assertIn('ValueError: boom',result['output'])
        self.assertEqual(result['debug']['locals']['name'],"'Ada'")
        job.send({'action':'continue'});self.wait(job,lambda s:s['status']!='running')
        self.assertEqual(job.code,1)
    def test_debug_step_in_and_out(self):
        job=self.start('debug','def add(x):\n    y=x+1\n    return y\na=add(4)\nprint(a)\n', breakpoints={'sample.py':[4]})
        first=self.wait(job,lambda s:s['debug']['state']=='paused');job.send({'action':'continue'})
        at_call=self.wait(job,lambda s:s['debug'].get('line')==4 and s['debug']['sequence']>first['debug']['sequence'])
        job.send({'action':'step'})
        inside=self.wait(job,lambda s:s['debug'].get('line')==2 and s['debug']['sequence']>at_call['debug']['sequence'])
        self.assertEqual(len(inside['debug']['frames']),2)
        job.send({'action':'out'})
        outside=self.wait(job,lambda s:s['debug'].get('line')==5 and s['debug']['sequence']>inside['debug']['sequence'])
        self.assertEqual(outside['debug']['locals']['a'],'5')
        job.send({'action':'continue'});self.wait(job,lambda s:s['status']!='running')
    def test_debug_pause_and_cancel(self):
        job=self.start('debug','import time\nx=0\nwhile True:\n    x+=1\n    time.sleep(.01)\n')
        first=self.wait(job,lambda s:s['debug']['state']=='paused');job.send({'action':'continue'})
        self.wait(job,lambda s:s['debug']['state']=='running');job.send({'action':'pause'})
        self.wait(job,lambda s:s['debug']['state']=='paused' and s['debug']['sequence']>first['debug']['sequence'])
        job.stop();self.wait(job,lambda s:s['status']=='cancelled')
    def test_breakpoints_reject_escape(self):
        with self.assertRaises(ValueError): self.ws.validate_breakpoints({'../outside.py':[1]})
        with self.assertRaises(ValueError): self.start('debug','x=1\n',breakpoints={'sample.py':[-1]})
    def test_completion_never_executes_project_and_handles_incomplete_code(self):
        source='import evil\ndef greet(name):\n    pass\ngre'
        result=complete(source,len(source))
        self.assertIn('greet',[i['label'] for i in result['items']])
        self.assertEqual(result['start'],len(source)-3)
        self.assertIn('print',[i['label'] for i in complete('pri',3)['items']])
        self.assertIn('append',[i['label'] for i in complete('items=[]\nitems.ap',len('items=[]\nitems.ap'))['items']])
        self.assertIn('print',[i['label'] for i in complete('def broken(:\npri',len('def broken(:\npri'))['items']])

if __name__=='__main__': unittest.main()
