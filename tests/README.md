# MiniAgent Test Guide

The test suite is organized by product capability. The repository-level pytest configuration
runs the deterministic local gate by default and keeps performance and real-API evaluation tests
explicitly opt-in.

## Layout

```text
tests/
|- agent/           # planning, execution, reflection, confirmation, agent types
|- runtime/         # assistant composition, engine, lifecycle, config, self-optimization
|- cli/             # CLI, TUI, commands, welcome and platform interaction
|- feishu/          # messaging, routing, cards, Docx, Bitable and Drive
|- llm/             # gateways, providers, transports and request protocols
|- memory/          # memory, knowledge, history, indexes and persistence
|- scheduling/      # scheduled tasks, cron and timezone behavior
|- session/         # sessions, locks, workspaces and background cleanup
|- skills/          # skill loading, registration, installation and ClawHub
|- tools/           # filesystem, exec, web, browser, MCP and sandbox behavior
|- observability/   # tracing, logs, monitoring and statistics
|- quality/         # architecture, docs, packaging and public API contracts
|- integration/     # workflows that genuinely cross capability boundaries
|- performance/     # opt-in benchmarks, performance support and baselines
|- evaluation/      # opt-in real-API evaluation and samples
|- support/         # shared test doubles and factories
`- conftest.py      # suite-wide isolation fixtures only
```

## Commands

```bash
# Complete deterministic local gate (evaluation and perf are excluded by pyproject.toml).
python -m pytest

# Run one capability directly.
python -m pytest tests/agent
python -m pytest tests/feishu
python -m pytest tests/session

# Select a file or test as usual.
python -m pytest tests/agent/test_planner_full_flow.py -k normalization

# Opt-in performance and evaluation suites.
python -m pytest tests/performance -m perf
python -m pytest tests/evaluation -m evaluation

# Coverage for the deterministic gate.
python -m pytest --cov=miniagent --cov-branch --cov-report=term-missing

# Inspect collection without maintaining a hard-coded test count.
python -m pytest --collect-only
```

Real API tests additionally require `MINIAGENT_REAL_API_STRESS=1` and valid provider credentials.
Generated API performance output belongs in `workspaces/logs/perf/`, while hand-maintained
references belong in `tests/performance/baselines/`.

## Organization Rules

- Put a test in the capability that owns the observable behavior. Use `integration/` only when no
  single capability owns the scenario.
- Give each behavior one canonical test home. Preserve unique regression cases when merging files,
  but remove assertions already covered by the canonical behavior test.
- Keep `conftest.py` limited to suite-wide isolation. Shared doubles and lifecycle factories belong
  in `support/`; capability-specific helpers stay beside their tests.
- Do not import private helpers from another `test_*.py` module.
- Parameterize cases only when control flow is identical, and use stable case IDs.
- Mock network, SDK and LLM calls unless a test is explicitly in `evaluation/`.
- Keep generated logs, traces, coverage files and performance snapshots out of the repository.
