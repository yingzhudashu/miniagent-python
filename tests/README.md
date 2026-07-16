# MiniAgent Test Guide

This directory contains MiniAgent's local unit, integration, performance smoke, and gated
evaluation tests.

## Layout

```text
tests/
|- conftest.py             # suite-wide isolation fixtures only
|- llm_helpers.py          # deterministic LLM gateway doubles
|- executor_helpers.py     # explicit executor collaborator factories
|- *_helpers.py            # narrowly scoped shared test utilities
|- test_*.py               # default local suite, organized by behavior
|- evaluation/             # explicit opt-in API and evaluation tests
|- perf_baselines/         # hand-maintained reference baselines
`- performance/            # optional benchmark helpers
```

## Common Commands

```bash
# Default local gate, matching CI intent.
python -m pytest tests/ -q -m "not evaluation"

# Include evaluation tests; real API tests still require explicit environment gates.
python -m pytest tests/ -q

# Synthetic local performance smoke.
python -m pytest tests/test_perf_synthetic.py -q -m perf --durations=13

# Coverage report; generated .coverage/htmlcov files are ignored artifacts.
python -m pytest tests/ -q -m "not evaluation" \
  --cov=miniagent --cov-report=html --cov-report=term-missing

# Inspect the current collected tests. Do not hard-code this count in documentation.
python -m pytest tests/ --collect-only -q
```

## Markers

Markers are declared in `pyproject.toml`:

- `evaluation`: gated tests under `tests/evaluation`, often using network/API access.
- `perf`: deterministic local performance smoke tests.
- `slow`: tests that may take more than a few seconds.
- `dot_help_dispatch`: real `.help` dispatch path coverage.

Most tests intentionally avoid `unit` and `integration` markers. Use explicit file paths for
focused runs instead of relying on undeclared markers.

## Test Organization

- Give each behavior one canonical test home. Do not repeat a unit-level assertion in a consumer
  test unless the consumer exercises a distinct integration path.
- When merging test files, delete the superseded files in the same change. Preserve unique cases
  in a focused behavior file before removing an aggregate file.
- Put regressions beside the behavior they protect. Use a dedicated regression file only when the
  scenario crosses multiple subsystem boundaries.
- Keep `conftest.py` limited to fixtures that provide suite-wide isolation. Put domain-specific
  factories in explicit helper modules and import them only where needed.
- Extract a helper only when at least three callers share the same setup or when it owns a complex
  lifecycle. Inline one-off setup so the behavior remains visible in the test.
- Parameterize cases only when control flow is identical and inputs or expected outputs vary. Add
  stable `ids` that identify the behavior represented by each case.
- Test observable behavior. Avoid tests that only check `callable`, positive constants, or a helper
  that reimplements production logic inside the test.
- Network, SDK, and LLM tests must use deterministic fakes or mocks and explicitly clean up clients,
  tasks, temporary state, and process-level hooks.

## Real API Tests

Real API tests are outside the default local and CI gate. Run them only with explicit opt-in:

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
- Keep generated logs, traces, coverage files, and performance snapshots out of the repository.
