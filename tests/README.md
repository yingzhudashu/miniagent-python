# MiniAgent Test Guide

This directory contains the local unit, integration, performance smoke, and gated evaluation tests
for Mini Agent Python.

## Layout

```text
tests/
├── conftest.py                 # shared fixtures and test isolation
├── test_*.py                   # default local test suite
├── evaluation/                 # explicit opt-in API/evaluation tests
│   ├── conftest.py             # evaluation marker wiring
│   ├── README_API_PERF.md      # real API smoke instructions
│   └── samples/                # offline evaluation samples
├── perf_baselines/             # small hand-maintained reference baselines only
└── performance/                # optional benchmark helpers
```

## Common Commands

```bash
# Default local gate, matching CI intent.
python -m pytest tests/ -q -m "not evaluation"

# Include evaluation tests; real API tests still require explicit environment gates.
python -m pytest tests/ -q

# Synthetic local performance smoke.
python -m pytest tests/test_perf_synthetic.py -q -m perf --durations=13

# Coverage report; generated .coverage/htmlcov are process artifacts and are ignored.
python -m pytest tests/ -q -m "not evaluation" \
  --cov=miniagent --cov-report=html --cov-report=term-missing

# Current collected test count. Do not hard-code this number in docs without rerunning it.
python -m pytest tests/ --collect-only -q
```

## Markers

Project markers are declared in `pyproject.toml`:

- `evaluation`: gated tests under `tests/evaluation`, often using network/API access.
- `perf`: deterministic local performance smoke tests.
- `slow`: tests that may take more than a few seconds.
- `dot_help_dispatch`: real `.help` dispatch path coverage.

Most tests intentionally avoid `unit` / `integration` markers, so use explicit file paths for
focused runs instead of relying on undeclared marker names.

## Real API Tests

Real API tests are not part of the default local or CI gate. Run them only with an explicit opt-in:

```powershell
$env:MINIAGENT_REAL_API_STRESS = "1"
$env:MINIAGENT_REAL_API_PERF_DIR = "workspaces/logs/perf"
python -m pytest tests/evaluation/test_perf_real_api.py -q
```

Generated real API output belongs in `workspaces/logs/perf/`, not in `tests/perf_baselines/`.

## Test Hygiene

- Keep tests deterministic and isolated with temporary state directories.
- Mock network, SDK, and LLM clients unless a test is explicitly marked `evaluation`.
- Clear process-level singletons and trace hooks when a test mutates them.
- Keep generated logs, traces, coverage files, and perf snapshots out of the repository.
