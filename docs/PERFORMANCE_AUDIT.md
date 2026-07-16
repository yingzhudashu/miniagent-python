# Performance Audit Ledger

This ledger proves the exact tracked revision covered by the repository-wide 
line scan and Python AST review. Regenerate it after reviewed files change.

- Files reviewed: 743
- Lines reviewed: 142137
- Categories: `{"ci": 2, "documentation": 18, "packaged-template": 25, "project-config": 5, "runtime": 336, "script": 15, "test": 342}`
- Finding classes: `{"async-sync-io-candidate": 49, "large-module": 48, "review-markers": 17}`

The machine-readable per-file hashes, metrics, findings, and reviewed line counts are in 
`docs/performance-audit.json`. `python scripts/performance_audit.py --check` fails when 
the tracked review surface changes.
