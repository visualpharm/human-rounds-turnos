"""Test-only observability wrapper for a real ``worker.py`` process.

This is deliberately *not* imported by production code.  It installs small
wrappers around the installed procrastinate 3.x methods and then executes the
real worker entrypoint supplied on argv.  ``SIGUSR1`` asks for a value-free
snapshot of coroutine await chains; it never changes the worker's shutdown
or cancellation behaviour.
"""
import asyncio
import os
import signal
import sys


def _trace(phase, edge):
    print(f"[worker-diagnostic] {phase}:{edge}", file=sys.stderr, flush=True)


def _frame_line(frame):
    """Return code location only: never arguments, locals, task names or values."""
    code = frame.f_code
    return f"{code.co_name} {os.path.basename(code.co_filename)}:{frame.f_lineno}"


def _await_chain(awaitable):
    """Yield the coroutine/generator frames currently being awaited."""
    seen = set()
    current = awaitable
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame = getattr(current, "cr_frame", None) or getattr(current, "gi_frame", None)
        if frame is not None:
            yield _frame_line(frame)
        current = (getattr(current, "cr_await", None)
                   or getattr(current, "gi_yieldfrom", None))


def dump_await_chains():
    """Bounded diagnostic for a supervisor timeout, with no runtime values."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print("[worker-diagnostic] await-chain unavailable:no-running-loop",
              file=sys.stderr, flush=True)
        return
    print("[worker-diagnostic] await-chain:begin", file=sys.stderr, flush=True)
    # Cap both task count and depth: the timeout probe itself must not become
    # another unbounded shutdown phase.
    for number, task in enumerate(list(asyncio.all_tasks(loop))[:16], start=1):
        for depth, line in enumerate(_await_chain(task.get_coro()), start=1):
            if depth > 12:
                break
            print(f"[worker-diagnostic] await task[{number}] depth[{depth}] {line}",
                  file=sys.stderr, flush=True)
    print("[worker-diagnostic] await-chain:end", file=sys.stderr, flush=True)


def _request_dump(signum, frame):
    # A signal can interrupt a stderr write or mutation of the task registry.
    # Defer collection to an ordinary loop callback, never re-enter those
    # operations from the synchronous handler itself.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        os.write(2, b"[worker-diagnostic] await-chain unavailable:no-running-loop\n")
    else:
        loop.call_soon(dump_await_chains)


def install():
    from procrastinate.manager import JobManager
    from procrastinate.worker import Worker

    def traced(method, phase):
        async def wrapper(self, *args, **kwargs):
            # Method identities are intentional; args/kwargs (which can carry
            # worker IDs or application data) are never rendered.
            _trace(phase, "enter")
            try:
                return await method(self, *args, **kwargs)
            finally:
                _trace(phase, "exit")
        return wrapper

    JobManager.prune_stalled_workers = traced(
        JobManager.prune_stalled_workers, "prune")
    JobManager.register_worker = traced(JobManager.register_worker, "register")
    JobManager.unregister_worker = traced(JobManager.unregister_worker, "unregister")
    Worker._shutdown = traced(Worker._shutdown, "shutdown")
    signal.signal(signal.SIGUSR1, _request_dump)
    print("[worker-diagnostic] ready", file=sys.stderr, flush=True)


def main():
    if len(sys.argv) != 2 or not sys.argv[1].endswith("worker.py"):
        raise SystemExit("usage: worker_diagnostics_bootstrap.py /absolute/path/worker.py")
    install()
    # Execute the actual source as its executable entrypoint.  Keeping its
    # pathname in argv also lets the suite's worker.py process-leak gate see
    # this child exactly as it sees the ordinary WorkerProc child.
    worker_path = sys.argv[1]
    # the project root, never the application package's own directory (app/worker.py): that
    # would make `core` importable a second time as a top-level module
    root = os.path.dirname(os.path.dirname(worker_path))
    if root not in sys.path:
        sys.path.insert(0, root)
    sys.argv = [worker_path]
    with open(worker_path, "rb") as source_file:
        source = compile(source_file.read(), worker_path, "exec")
    globals_dict = {"__name__": "__main__", "__file__": worker_path,
                    "__package__": None, "__cached__": None}
    exec(source, globals_dict)


if __name__ == "__main__":
    main()
