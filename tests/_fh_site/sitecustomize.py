"""Test-only: let the harness ask a worker child for all-thread stacks with a
non-fatal signal (SIGUSR2). faulthandler.register is a C handler, so it writes
even when the main thread is stuck in C code, and the process does not abort
(SIGABRT makes macOS write a crash report and show a window for every dump).
Reached only through PYTHONPATH set by tests/_workerproc.WorkerProc.

This file shadows any sitecustomize the interpreter's environment ships (a
venv or a managed runner may use it to set up its paths), so it runs that one
afterwards."""
import faulthandler
import importlib.machinery
import os
import signal
import sys

faulthandler.register(signal.SIGUSR2, all_threads=True)

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.machinery.PathFinder.find_spec(
    "sitecustomize",
    [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _here])
if _spec is not None and _spec.loader is not None:
    import importlib.util
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
