# Legacy-cleanup coverage evidence

The maintainer's row 1.3 requires coverage not to fall across the legacy cleanup and a
separate coherent commit for each retired area. The comparable snapshots use
the pre-cleanup ancestor `afb3b0b5` and the post-cleanup verification commit
`ec10f9d7`. Both were measured from detached worktrees with the same Python
3.14 and Node 20 runtimes.

## Coverage comparison

| Metric | Before `afb3b0b5` | After `ec10f9d7` | Change |
|---|---:|---:|---:|
| Python lines | 32,672 / 39,816 (80.22%) | 30,746 / 36,575 (81.92%) | +1.70 points |
| Python branches | 9,362 / 12,580 (74.42%) | 8,779 / 11,672 (75.21%) | +0.79 points |
| JavaScript lines | 89.67% | 90.09% | +0.42 points |
| JavaScript branches | 87.45% | 87.70% | +0.25 points |
| Production JavaScript functions | 335 / 408 (82.11%) | 329 / 391 (84.14%) | +2.03 points |

Python coverage came from `coverage.py` JSON after running every top-level
`test/test_*.py` file in a fresh process and combining the data. The isolated
coverage harness deliberately supplied PostgreSQL to every file, so a few
tests whose subject is an *unavailable* database reported expectation
mismatches. These measurement-only mismatches occurred outside the product
gate. The full suite was run separately with its normal managed fixtures at
`8031817e`: 5,258 tests passed with 99 declared skips.

Node's built-in aggregate report includes test files, so its function column
is not a stable production metric when test helpers change. The production
function row above instead merges V8 function ranges by source location and
excludes `test/` and `scripts/`; a function counts as covered when any test
process executes its outer range.

Raw reports are retained in the auditor's local artifacts directory as
`python-{afb3b0b5,ec10f9d7}.json` and
`node-{afb3b0b5,ec10f9d7}.log`.

## Area commits

- `2d534458` — first two legacy transports and source record
- `4da30ff0` — patient web handlers
- `c166ed3a` — admin handlers
- `6f63f4fd` — authentication and AI handlers
- `b60f70fd` — core handlers
- `4d0e65e9` — appointment-engine handlers
- `b04d7f18` — intake and chat handlers
- `0b0fa92c` — request-view adapters
- `c6bf6c52` — MCP handler exchange
- `f60dc0ee` — demo account native path
- `5bdb877d` — chat booking facades

Each commit is independently inspectable in `git show --stat <sha>` and keeps
the corresponding behavior tests in the same coherent change.
