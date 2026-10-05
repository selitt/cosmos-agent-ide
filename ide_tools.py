"""Local Python completions and interactive process sessions for Cosmos."""
import ast
import builtins
import codecs
import inspect
import json
import keyword
import os
from pathlib import Path
import pty
import re
import secrets
import signal
import subprocess
import sys
import threading


def complete(source, offset):
    if not isinstance(source, str) or len(source.encode('utf-8')) > 1024 * 1024:
        raise ValueError('Слишком большой файл')
    offset = max(0, min(int(offset), len(source)))
    before = source[:offset]
    match = re.search(r'([A-Za-z_]\w*)?$', before)
    prefix = match.group() if match else ''
    names = {name: ('keyword', '') for name in keyword.kwlist}
    for name in dir(builtins):
        if not name.startswith('_'):
            obj = getattr(builtins, name)
            doc = (inspect.getdoc(obj) or '').split('\n')[0]
            names[name] = ('builtin', doc[:250])
    for name in re.findall(r'\b[A-Za-z_]\w*\b', source):
        names.setdefault(name, ('symbol', 'Символ в открытом файле'))
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
                names[node.name] = ('function', node.name + '(' + ', '.join(args) + ')')
            elif isinstance(node, ast.ClassDef):
                names[node.name] = ('class', 'class ' + node.name)
    except SyntaxError:
        pass
    # Attribute hints inspect standard builtin types, never import or execute project code.
    attr = re.search(r'([A-Za-z_]\w*)\.([A-Za-z_]\w*)?$', before)
    if attr:
        owner, prefix = attr.group(1), attr.group(2) or ''
        types = {'str': str, 'list': list, 'dict': dict, 'set': set}
        literal = re.search(r'\b' + re.escape(owner) + r'\s*=\s*([\[\{\"\'])', source)
        inferred = {'[': list, '{': dict, '"': str, "'": str}.get(literal.group(1)) if literal else None
        obj = types.get(owner) or inferred
        names = {n: ('method', (inspect.getdoc(getattr(obj, n)) or '').split('\n')[0][:250])
                 for n in dir(obj) if not n.startswith('_')} if obj else {}
    items = [{'label': n, 'kind': kind, 'detail': detail} for n, (kind, detail) in sorted(names.items())
             if n.startswith(prefix) and n != prefix][:50]
    return {'start': offset - len(prefix), 'end': offset, 'items': items}


class Session:
    def __init__(self, root, args, kind, terminal=False, initial=None):
        self.id = secrets.token_hex(12)
        self.kind = kind
        self.status = 'running'
        self.output = ''
        self.answer = ''
        self.code = None
        self.lock = threading.RLock()
        self.debug = {'state': 'running', 'frames': [], 'locals': {}, 'sequence': 0}
        self.terminal = terminal
        self.master = None
        env = os.environ.copy()
        env.pop('GEMINI_API_KEY', None)
        env.pop('GOOGLE_API_KEY', None)
        env['TERM'] = 'dumb'
        env['PYTHONUNBUFFERED'] = '1'
        if terminal:
            master, slave = pty.openpty()
            self.master = master
            try:
                wrapped = [sys.executable, str(Path(__file__).with_name('terminal_runner.py')), *args]
                self.proc = subprocess.Popen(wrapped, cwd=root, env=env, stdin=slave, stdout=slave,
                                             stderr=slave, start_new_session=True)
            except BaseException:
                os.close(master)
                self.master = None
                raise
            finally:
                os.close(slave)
        else:
            self.proc = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         start_new_session=True)
        if initial is not None:
            self.send(initial)
        threading.Thread(target=self.read, daemon=True).start()

    def append(self, value):
        with self.lock:
            self.output = (self.output + value)[-200_000:]

    def read(self):
        try:
            if self.terminal:
                decoder = codecs.getincrementaldecoder('utf-8')('replace')
                while True:
                    try:
                        chunk = os.read(self.master, 8192)
                    except OSError:
                        break
                    if not chunk:
                        break
                    self.append(decoder.decode(chunk))
                self.append(decoder.decode(b'', final=True))
            else:
                for line in self.proc.stdout:
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        self.append(line.decode('utf-8', 'replace'))
                        continue
                    if event.get('event') == 'output':
                        self.append(event.get('text', ''))
                    elif event.get('event') in {'paused', 'running', 'input'}:
                        with self.lock:
                            self.debug = {**event, 'state': event['event'], 'sequence': self.debug['sequence'] + 1}
                    elif event.get('event') == 'error':
                        self.append(event.get('text', ''))
            code = self.proc.wait()
            with self.lock:
                self.code = code
                if self.status != 'cancelled':
                    self.status = 'done' if code == 0 else 'error'
                self.debug['state'] = self.status
        finally:
            with self.lock:
                if self.master is not None:
                    os.close(self.master)
                    self.master = None
            if not self.terminal:
                self.proc.stdout.close()
                self.proc.stdin.close()

    def send(self, value):
        with self.lock:
            if self.status != 'running' or self.proc.poll() is not None:
                raise ValueError('Процесс уже завершён')
            raw = value.encode('utf-8') if isinstance(value, str) else (json.dumps(value) + '\n').encode('utf-8')
            if len(raw) > 65536:
                raise ValueError('Ввод слишком большой')
            if self.terminal:
                os.write(self.master, raw)
            else:
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()

    def snapshot(self):
        with self.lock:
            return {'id': self.id, 'kind': self.kind, 'status': self.status, 'output': self.output,
                    'answer': '', 'code': self.code, 'agents': {}, 'debug': dict(self.debug)}

    def stop(self):
        with self.lock:
            if self.proc.poll() is not None or self.status == 'cancelled':
                return
            self.status = 'cancelled'
            if self.terminal and self.master is not None:
                try:
                    foreground = os.tcgetpgrp(self.master)
                    if foreground > 0 and foreground != self.proc.pid:
                        os.killpg(foreground, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        def kill():
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=kill, daemon=True).start()
