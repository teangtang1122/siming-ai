# Siming 3.0 Performance Baseline

The RC gate measures deterministic startup work without contacting a model or provider.

| Measurement | RC budget |
| --- | ---: |
| Tool registry plus PromptSpec compilation | 10 seconds |
| Application factory plus OpenAPI generation | 15 seconds |
| Fresh SQLite/Alembic bootstrap | 30 seconds |
| Combined process total | 45 seconds |

Run:

```powershell
backend\.venv\Scripts\python.exe scripts\run-performance-baseline.py `
  --output .build\performance.json
```

The baseline records platform, Python version, elapsed time, 160-tool registry count, PromptSpec count, API route/path counts, schema revision and table count. Budgets are intentionally generous enough for GitHub runners; they detect architectural regressions rather than microbenchmark noise.

Reference Windows measurement on 2026-07-18:

- Tool and prompt catalog: 2.40 seconds.
- Application and OpenAPI: 1.25 seconds.
- Fresh database bootstrap: 0.88 seconds.
- Total: 4.53 seconds.

Real model latency, long-running cataloging and frontend rendering are covered by operation/E2E tests, not this deterministic startup gate.
