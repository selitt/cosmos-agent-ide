"""Debug protocol in a separate interpreter; project code never runs in the IDE server."""
import builtins
import json
from pathlib import Path
import queue
import sys
import threading
import traceback

wire = sys.stdout
commands = queue.Queue()
write_lock = threading.Lock()

def emit(event, **data):
    with write_lock:
        wire.write(json.dumps({'event': event, **data}, ensure_ascii=False) + '\n')
        wire.flush()

class Output:
    encoding = 'utf-8'
    def write(self, text):
        emit('output', text=str(text))
        return len(text)
    def flush(self): pass
    def isatty(self): return False

class Debugger:
    def __init__(self, root, breaks):
        self.root = root
        self.breaks = breaks
        self.mode = 'step'
        self.anchor = None

    def frames(self, frame):
        frames = []
        while frame:
            try:
                path = Path(frame.f_code.co_filename).resolve()
                if path.is_relative_to(self.root) and path != Path(__file__).resolve():
                    frames.append((frame, str(path.relative_to(self.root))))
            except (OSError, ValueError): pass
            frame = frame.f_back
        return frames

    def pause(self, frame, reason='line'):
        frames = self.frames(frame)
        values = {}
        for name, value in list(frame.f_locals.items())[:100]:
            if name.startswith('__'): continue
            try: values[name] = repr(value)[:600]
            except BaseException: values[name] = '<repr недоступен>'
        emit('paused', reason=reason, path=frames[0][1] if frames else '', line=frame.f_lineno,
             frames=[{'path': p, 'line': f.f_lineno, 'name': f.f_code.co_name} for f, p in frames], locals=values)
        while True:
            cmd = commands.get()
            action = cmd.get('action')
            if action == 'breakpoints':
                self.breaks = cmd['breakpoints']
            elif action in {'continue', 'step', 'next', 'out'}:
                self.mode = action
                self.anchor = frame
                emit('running')
                return
            elif action == 'stop':
                raise SystemExit(0)

    def trace(self, frame, event, arg):
        frames = self.frames(frame)
        if not frames or frames[0][0] is not frame:
            return None
        while True:
            try: cmd = commands.get_nowait()
            except queue.Empty: break
            if cmd.get('action') == 'pause': self.mode = 'step'
            elif cmd.get('action') == 'stop': raise SystemExit(0)
            elif cmd.get('action') == 'breakpoints': self.breaks = cmd['breakpoints']
        if event == 'line':
            at_break = frame.f_lineno in self.breaks.get(frames[0][1], [])
            in_stack = any(f is self.anchor for f, _ in frames)
            should_stop = (self.mode == 'step' or at_break or
                           (self.mode == 'next' and (frame is self.anchor or not in_stack)) or
                           (self.mode == 'out' and not in_stack))
            if should_stop:
                self.pause(frame, 'breakpoint' if at_break else 'line')
        return self.trace


def read_commands():
    for line in sys.stdin:
        try: commands.put(json.loads(line))
        except ValueError: pass
    commands.put({'action': 'stop'})


def user_input(prompt=''):
    if prompt: emit('output', text=str(prompt))
    emit('input')
    while True:
        cmd = commands.get()
        if cmd.get('action') == 'input': return cmd.get('text', '')
        if cmd.get('action') == 'stop': raise SystemExit(0)


def main():
    config = json.loads(sys.stdin.readline())
    root = Path(config['root']).resolve()
    path = Path(config['path']).resolve()
    threading.Thread(target=read_commands, daemon=True).start()
    debugger = Debugger(root, config.get('breakpoints', {}))
    sys.stdout = sys.stderr = Output()
    builtins.input = user_input
    sys.argv = [str(path), *config.get('args', [])]
    sys.path.insert(0, str(path.parent))
    namespace = {'__name__': '__main__', '__file__': str(path), '__builtins__': builtins}
    try:
        code = compile(path.read_bytes(), str(path), 'exec')
        sys.settrace(debugger.trace)
        exec(code, namespace, namespace)
    except SystemExit as exc:
        sys.settrace(None)
        if exc.code not in (None, 0):
            emit('error', text=str(exc.code) + '\n')
            return 1
    except BaseException:
        sys.settrace(None)
        info = sys.exc_info()
        emit('error', text=''.join(traceback.format_exception(*info)))
        last = info[2]
        while last.tb_next: last = last.tb_next
        debugger.pause(last.tb_frame, 'exception')
        return 1
    finally:
        sys.settrace(None)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
