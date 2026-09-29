"""OpenFFBoard configurator launcher with an automatic stack dump.

Runs main.py exactly like `python main.py`, but arms faulthandler so that after
25 seconds every thread's Python stack is printed right here in the console.
The UI freeze happens during MainUi construction, so the dump will show the
exact blocking line.

faulthandler.register() is Unix only, so on Windows this uses
dump_traceback_later() instead.
"""
import faulthandler
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

# dump all thread stacks after 25s (stderr = this console)
faulthandler.enable()
faulthandler.dump_traceback_later(25, exit=False, file=sys.stderr)

print("=" * 72, flush=True)
print("OpenFFBoard configurator - debug launcher", flush=True)
print("waiting for the freeze. In ~25s the python stack is printed below.", flush=True)
print("=" * 72, flush=True)

if __name__ == "__main__":
    sys.argv[0] = "main.py"
    with open("main.py", encoding="utf-8") as fh:
        code = compile(fh.read(), "main.py", "exec")
    exec(code, {"__name__": "__main__", "__file__": "main.py"})