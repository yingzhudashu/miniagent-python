# Performance Audit Ledger

This ledger proves the exact tracked revision covered by the repository-wide 
line scan and Python AST review. Regenerate it after reviewed files change.

- Files reviewed: 742
- Lines reviewed: 141880
- Categories: `{"ci": 2, "documentation": 18, "packaged-template": 25, "project-config": 5, "runtime": 339, "script": 15, "test": 338}`
- Finding classes: `{"async-sync-io-candidate": 49, "large-module": 49, "review-markers": 16}`

The machine-readable per-file hashes, metrics, findings, and reviewed line counts are in 
`docs/performance-audit.json`. `python scripts/performance_audit.py --check` fails when 
the tracked review surface changes.
