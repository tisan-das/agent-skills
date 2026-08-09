# ce-ops fixture

Synthetic CloudWatch Logs Insights export (shape: `[{ "@timestamp", "@message" }]`)
for the `ce-ops` behavioral evals. It reproduces the canonical PrivateLink private-DNS
verification timeout with realistic-but-fake identifiers, and deliberately includes one
piece of **secondary noise** — the ServiceNow `Org not found` 400 — that logs an error
but does not block provisioning. A correct triage separates the two.

The bundled parser should run against it:

```bash
python3 skills/ce-ops/scripts/parse_cloudwatch.py \
  evals/fixtures/ce-ops/logs-insights-results__01_.json --ce CEAMEXAMPLE10001X --timeline
```
