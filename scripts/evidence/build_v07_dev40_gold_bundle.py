"""Create a minimal v0.7 dev40 Gold annotation bundle; never decode non-dev abstracts."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evidence.freeze_v07_datasets import metadata_only_row, _skip_string, _skip_value, _skip_ws

CORPUS = ROOT / 'data/processed/saline_paddy_v056_full_enriched_20261003.jsonl'
IDENTITIES = ROOT / 'data/evidence_benchmarks/v07_dev40/identities.json'
SCHEMA = ROOT / 'schemas/evidence_matrix.schema.json'
CONTRACT = ROOT / 'docs/evidence_field_contract_v2.md'
INSTRUCTIONS = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work/GOLD_ANNOTATION_INSTRUCTIONS.md'
BUNDLE = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work'
MANIFEST = ROOT / 'data/evidence_benchmarks/v07_dev40_gold_work_manifest.json'


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def identity_rows(path: Path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(value, dict):
        value = value.get('identities', value.get('uids'))
    if not isinstance(value, list):
        raise ValueError('dev40_identity_registry_shape')
    return [row if isinstance(row, dict) else {'uid': str(row)} for row in value]


def raw_top_level_values(line: bytes, wanted: set[str]):
    """Return raw slices only for requested keys on one already-identified dev row."""
    data = line.strip()
    i = _skip_ws(data, 0)
    if data[i] != 123:
        raise ValueError('expected_json_object')
    i += 1
    found = {}
    while True:
        i = _skip_ws(data, i)
        if i >= len(data):
            raise ValueError('unterminated_json_object')
        if data[i] == 125:
            break
        key_end = _skip_string(data, i)
        key = json.loads(data[i:key_end].decode('utf-8'))
        i = _skip_ws(data, key_end)
        if data[i] != 58:
            raise ValueError('expected_json_colon')
        start = _skip_ws(data, i + 1)
        end = _skip_value(data, start)
        if key in wanted:
            found[key] = data[start:end]
        i = _skip_ws(data, end)
        if i < len(data) and data[i] == 44:
            i += 1
    return found


def extract_dev_records(corpus: Path, dev_uids: set[str]):
    found = {}
    with Path(corpus).open('rb') as stream:
        for line in stream:
            if not line.strip():
                continue
            metadata, _has_abstract = metadata_only_row(line)
            uid = metadata['uid']
            if uid not in dev_uids:
                continue
            raw = raw_top_level_values(line, {'abstract'})
            if 'abstract' not in raw:
                raise ValueError(f'dev_abstract_missing:{uid}')
            abstract = json.loads(raw['abstract'].decode('utf-8'))
            if not isinstance(abstract, str) or not abstract.strip():
                raise ValueError(f'dev_abstract_empty:{uid}')
            found[uid] = {
                'uid': metadata['uid'], 'doi': metadata['doi'], 'title': metadata['title'],
                'journal': metadata['source_title'], 'year': metadata['publish_year'],
                'document_types': metadata['document_types'], 'abstract': abstract,
            }
    if set(found) != dev_uids:
        raise ValueError(f'dev_identity_match:{len(found)}/{len(dev_uids)}')
    return found


def annotation_instructions():
    return '''# Gold Annotation Instructions — v0.7 Development Set

You are one independent human-quality Gold annotator. Annotate all 40 supplied records. Use only this bundle. Do not inspect parent directories, the repository, other annotations, model outputs, prompts, historical benchmark scores, or any other papers. Work independently; do not communicate your decisions to another annotator. Output one JSON object per input record in a JSONL file at the output path supplied by the launcher.

## Required output

Return exactly one record per UID with the existing Evidence Matrix v0.4 shape, validated against `evidence_matrix.schema.json`:

- `schema_version`: `"0.4"`; copy `uid`, `doi`, and `title` from the input.
- `screening`: `status="maybe"`, concise reason, confidence.
- `evidence`: all keys from the schema: `study_system`, `treatments`, `measurements`, `methods`, `findings`, `mechanisms_explicit`, `limitations_explicit`, `author_interpretations`.
- `evidence_support`: one JSON Pointer entry for each populated ordinary evidence leaf validated through the global support map. Do not add entries for `/evidence/findings/*` or `/evidence/author_interpretations/*`; these items carry their own nested exact anchors. Each anchor uses `source="abstract"`, exact verbatim `evidence_text`, zero-based Unicode character `start` and exclusive `end` offsets.
- `inference`: every existing inference array must be empty. Do not infer or personalize.

For `findings`, preserve the exact source quote and set `claim` to a verbatim substring of that quote; do not paraphrase, normalize, or add interpretation to `claim`. Use only schema-supported certainty values. For `author_interpretations`, preserve the exact anchored span and supported `claim_type`. `mechanisms_explicit` contains only explicit mechanism relations. Every extracted value must occur verbatim inside its own source quote, and all anchors must have exact offsets. Use empty arrays/null/`unknown` when unsupported. Do not fabricate or repair source text. Do not use title text as evidence.

## Approved ontology rules

Follow `evidence_field_contract_v2.md` exactly. Key boundaries:

- Ordinary soil/substrate descriptors qualify for `soil_type` only when tied to the actual sampled, experimental, site soil, or pot substrate; land use and application targets do not.
- `salinity_context` requires an explicit actual-system or treatment-induced link for Articles; keep background, application, and attributed Review scope distinct.
- Plant tissue/phenotype physiology, pigments, photosynthesis, antioxidant enzymes, ROS, proline, plant Na/K, and plant stress genes go to `measurements.plant_growth`.
- `measurements.other` is last resort after all named categories.
- Microbial functional genes, community assembly, and network ecology are `measurements.microbial`; an unattributed bulk soil enzyme is not automatically microbial.
- Measured carbon fractions/properties go to `carbon`; greenhouse-gas flux stays in `greenhouse_gases`.
- Separate fertilization nutrient input from amendment soil-conditioning role; avoid unsupported duplication.
- Field-scale variants map to `field`; a named model is a method and alone does not set `experimental_scale=model`.
- Findings record what was found. Interpretations require a distinct, explicit author meaning layer. Mechanisms require explicit X affects Y through/by/via Z or equivalent. Do not duplicate a finding mechanically. A span may support two layers only when they are semantically distinct.

## Output checks

Before finishing, verify 40 unique UIDs, no missing record, JSONL syntax, exact anchors and offsets, all inference arrays empty, and no additional keys. Do not include commentary or benchmark scores in the output file. Final response should report only the output path, record count, and any records you could not annotate reliably.
'''


def build(bundle: Path = BUNDLE, corpus: Path = CORPUS, identities: Path = IDENTITIES):
    bundle = Path(bundle).resolve()
    if bundle.exists():
        raise FileExistsError('Refusing to overwrite dev40 Gold work bundle')
    rows = identity_rows(identities)
    uids = {r['uid'] for r in rows}
    if len(rows) != 40 or len(uids) != 40:
        raise ValueError('expected_40_unique_dev_identities')
    records = extract_dev_records(corpus, uids)
    bundle.mkdir(parents=True)
    output_rows = [records[uid] for uid in sorted(uids)]
    corpus_out = bundle / 'abstracts.jsonl'
    corpus_out.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in output_rows), encoding='utf-8')
    shutil.copyfile(SCHEMA, bundle / 'evidence_matrix.schema.json')
    shutil.copyfile(CONTRACT, bundle / 'evidence_field_contract_v2.md')
    instruction_path = bundle / 'GOLD_ANNOTATION_INSTRUCTIONS.md'
    instruction_path.write_text(annotation_instructions(), encoding='utf-8')
    file_hashes = {p.name: sha(p) for p in sorted(bundle.iterdir()) if p.is_file()}
    manifest = {
        'bundle_role': 'v07_dev40_independent_gold_annotation_input',
        'record_count': len(output_rows), 'uids': sorted(uids),
        'files': file_hashes,
        'contract_sha256': sha(bundle / 'evidence_field_contract_v2.md'),
        'schema_sha256': sha(bundle / 'evidence_matrix.schema.json'),
        'contains_extraction_prompt': False,
        'contains_v06_worker_outputs': False,
        'contains_historical_holdout_predictions': False,
        'contains_future_holdout_identities': False,
        'future_holdout_abstracts_accessed': False,
    }
    Path(MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle', type=Path, default=BUNDLE)
    ap.add_argument('--corpus', type=Path, default=CORPUS)
    ap.add_argument('--identities', type=Path, default=IDENTITIES)
    a = ap.parse_args()
    print(json.dumps(build(a.bundle, a.corpus, a.identities), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
