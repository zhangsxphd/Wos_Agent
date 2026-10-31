"""Compare the frozen analyst reference with raw and conservative screening states."""
import json
import sys
from pathlib import Path

project = Path(sys.argv[1])
out = Path(__file__).resolve().parent
reference = json.loads((out / 'reference_sample.json').read_text(encoding='utf-8'))['rows']
partial = project / 'screening.json'
if not partial.exists():
    partial = project / 'screening_partial.json'
final = {r['source_id']: r for r in json.loads(partial.read_text(encoding='utf-8'))['decisions']}
raw = {}
for path in sorted((project / 'model_calls').glob('*_screen/response.txt')):
    raw.update({r['source_id']: r for r in json.loads(path.read_text(encoding='utf-8'))['decisions']})
rows = []
for ref in reference:
    key = ref['source_id']
    item = dict(ref)
    if key not in final or key not in raw:
        item.update(comparison='not_yet_screened')
    else:
        item.update(raw_status=raw[key]['status'], raw_reason=raw[key]['reason'], final_status=final[key]['status'])
        if ref['reference'] == 'direct_relevant' and final[key]['status'] == 'candidate_exclude':
            item['comparison'] = 'direct_relevant_excluded_serious'
        elif ref['reference'] == 'retain_uncertain_or_indirect' and final[key]['status'] == 'candidate_exclude':
            item['comparison'] = 'possible_loss_of_indirect_or_uncertain_evidence'
        elif ref['reference'] == 'title_indicates_out_of_scope' and final[key]['status'] != 'candidate_exclude':
            item['comparison'] = 'retained_conservatively_not_necessarily_error'
        else:
            item['comparison'] = 'no_reference_disagreement_identified'
    rows.append(item)
counts = {}
for row in rows:
    counts[row['comparison']] = counts.get(row['comparison'], 0) + 1
matrix = {}
for row in rows:
    if 'final_status' in row:
        key = row['reference'] + ' / ' + row['final_status']
        matrix[key] = matrix.get(key, 0) + 1
report = {'reference_size': len(reference), 'reference_by_final_status': matrix, 'compared': sum(r['comparison'] != 'not_yet_screened' for r in rows),
          'counts': counts, 'caveat': '助手预先逐题名判读，非独立人类Gold；富集抽样非代表性样本。间接证据排除不能一概算直接论文漏排。', 'rows': rows}
(out / 'reference_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k != 'rows'}, ensure_ascii=False, indent=2))
