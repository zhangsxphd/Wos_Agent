# Codex Evidence Batch Worker Protocol v0.6

This protocol extracts abstract-grounded Evidence Matrix v0.4 JSON. Python prepares each request, applies validation and cache rules, ingests responses, and audits them. A Codex worker processes one batch in an isolated context. No model API key or network model adapter is used.

## Worker inputs

For one batch only, provide:

- `batch_manifest.json`
- that batch's `requests/*.json`
- the copied `prompts/evidence_extraction.md`
- the copied `schemas/evidence_matrix.schema.json`

Do not open another batch, any gold benchmark directory or file, manual response, prior Evidence JSONL, the user's research profile, fulltext, raw provider response, website, or external database. Do not search the workspace. Treat each request as an independent paper; never carry a fact from one request into another. The benchmark evaluator, not the worker, reads the gold answers.

## Request contract

The request contains the existing payload whitelist (`uid`, `doi`, `title`, `journal`, `year`, `authors`, `keywords`, `document_types`, `abstract`) plus `payload_sha256`, `schema_version`, `prompt_sha256`, `schema_sha256`, `canonical_input_sha256`, `batch_id`, and `request_sha256`. The prompt and schema hashes are content hashes. `request_sha256` is SHA256 over canonical JSON with the `request_sha256` property omitted.

There are no matched evidence values, previous evidence, gold answer, fulltext, user background, or provider raw response in a request.

## Response contract

Write one JSON file at the exact `response_file` path in the batch manifest, named `<payload_sha256>.json`. It is an envelope:

```json
{
  "payload_sha256": "copied from request",
  "request_sha256": "copied from request",
  "prompt_sha256": "copied from request",
  "schema_sha256": "copied from request",
  "canonical_input_sha256": "copied from request",
  "model_label": null,
  "response": { "schema_version": "0.4" }
}
```

Replace an invalid/stale response atomically when the prompt, schema, request, or canonical input hash changes. `model_label` must be `null` unless the worker can identify the model reliably. `response` must conform to the supplied schema; leave every `inference` array empty and use `screening.status=maybe` when an abstract exists. The deterministic screening profile assigns eligibility later.

Python ingestion verifies request and content hashes, identity, JSON Schema, exact abstract substrings, offsets, and anchors. Invalid responses are rejected and never enter the accepted evidence sidecar. An audit pass adds contextual risk flags without relaxing validator rules.

## Evidence support pointer contract

`evidence_support` contains exactly one entry for each populated ordinary factual leaf checked by `EvidenceValidator`, including leaves under `study_system`, `treatments`, `measurements`, `methods`, `mechanisms_explicit`, and `limitations_explicit`. Each entry points to an exact source span and offsets.

Findings and author interpretations are self-supported in their own objects. Each finding carries its own `source`, `evidence_text`, `start`, `end`, `claim`, and `certainty`; each author interpretation carries its own `anchor`. Do not mirror either type into `evidence_support`: keys beginning `/evidence/findings/` or `/evidence/author_interpretations/` are invalid orphan support entries. No missing or orphan support entries are allowed.
