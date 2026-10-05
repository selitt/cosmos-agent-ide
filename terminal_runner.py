"""Acquire a controlling terminal after Popen created a new session, then exec."""
import fcntl
import os
import sys
import termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
