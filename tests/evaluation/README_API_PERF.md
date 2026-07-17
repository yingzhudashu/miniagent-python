# Real API Performance Smoke

These tests exercise the configured OpenAI-compatible API and are intentionally excluded from
default CI. They can incur API cost and require an explicit opt-in.

## Configuration

Use `config.user.json` or environment variables. Do not commit secrets.

```json
{
  "model": {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini"
  },
  "secrets": {
    "openai_api_key": "your-real-api-key"
  }
}
```

`OPENAI_API_KEY` in the environment also works. Test output uses metrics only and must not include
full prompts, responses, or API keys.

## Run

```powershell
$env:MINIAGENT_REAL_API_STRESS = "1"
$env:MINIAGENT_REAL_API_PERF_DIR = "workspaces/logs/perf"
python -m pytest tests/evaluation/test_perf_real_api.py -m evaluation
Remove-Item Env:MINIAGENT_REAL_API_STRESS
Remove-Item Env:MINIAGENT_REAL_API_PERF_DIR
```

`MINIAGENT_REAL_API_PERF_DIR` defaults to `workspaces/logs/perf`, which is ignored by Git. Keep
generated `real-api-test-results.json`, `concurrent-test-results.json`, trace files, and snapshots
out of `tests/performance/baselines/`.

## Optional Local Comparison

`tests/performance/baselines/real-api-baseline.json` is a small, hand-maintained reference baseline. It is
not a raw test output file.

```powershell
python scripts/perf_profile_tracemalloc.py --inner-repeat 10 --json-out real-api-snapshot.json
python scripts/compare_perf_snapshots.py tests/performance/baselines/real-api-baseline.json real-api-snapshot.json
```

For the full performance workflow and trace policy, see
[docs/PERFORMANCE.md](../../docs/PERFORMANCE.md) and [docs/ENGINEERING.md](../../docs/ENGINEERING.md).
