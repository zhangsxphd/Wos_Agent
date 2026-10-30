# Full-text Codex Candidate batch

This worker invokes the official `codex exec --output-schema` CLI using an existing ChatGPT login. It supplies the admitted full text and rendered original PDF pages. Python dispatches tasks and screens output; it does not generate scientific extraction values from keywords.

Run from the repository root, using a Python environment with `jsonschema`:

```sh
.venv/bin/python scripts/pilot/codex_fulltext_queue.py --limit 74 --workers 3
```

The fixed local queue contains 75 papers: the initial validated execution plus 74 remaining papers. Historical 25 candidates and human decisions are not read into model prompts or modified. Completed per-paper states are source-hash checked and skipped. A completed response surviving an interruption is recovered without a new model call. Failed non-quota calls receive one retry in a new immutable attempt folder; a process-wide lock excludes duplicate queue runners. On a ChatGPT usage-limit response, the dispatcher stops sending new papers, preserves the blocked attempts, and waits for the server-reported reset before resuming automatically; it never purchases credits or uses a paid API fallback. Original responses, prompts, schema/input/image hashes, JSONL events and usage are retained under the ignored local `data/pilot/v08_codex_fulltext_20261030_v2/` directory.

The requested model is `gpt-6.1-sol`. A resolved server model is not claimed where the CLI events do not expose it. Login is forced to ChatGPT; the script removes API-key environment overrides and does not enable paid API fallback. Subscription usage is not assumed to be unlimited or cost-free. CLI connection retries may switch from WebSocket to HTTPS before inference succeeds.

The consolidated `index.html` orders review flags by severity and searches DOI/content. `papers.csv`, `fields.csv`, `results.csv`, `issues.csv` and JSONL files retain Candidate status. `statistics.json` separates execution status from reported/uncertain/not-reported fields. A JSON Schema-valid response is not a scientific validation pass. Numeric-token membership is only a screening check, not proof that a value belongs to the correct treatment or table cell. Table alignment, significance, derived values without explicit operands, uncertain replication, and model-reported source conflicts stay flagged for review. Unique source-anchor relocation is recorded as a proposed sidecar correction; original outputs are never rewritten. No model accuracy or Gold is created.

Original PDFs, complete article text, local credentials and detailed local logs remain excluded from Git. The GitHub update contains executable code, checks and aggregate progress only; the local HTML links to the retained source outputs.

For a frozen, source-bound review snapshot, reuse the existing pilot review workbench with `python -m scripts.pilot.build_batch_review --output data/reviews/<new-version>`. It groups flags by paper and candidate, orders high severity first, and exports human decisions without changing Gold. Existing historical decisions are never loaded or replaced. A snapshot is not automatically refreshed while a human is reviewing it.

For an additional separate validation version run `python -m scripts.pilot.fulltext_validation_v2`. It checks simple treatment/value co-occurrence in parsed table lines, explicitly written percentage arithmetic, and obvious conflicts between identical significance letters and a significance claim. These screens never alter model output: parsed-line co-occurrence is not proof of the actual visual column, and arithmetic does not verify operand provenance. Run it before building the final review snapshot so any additional flags are included.
