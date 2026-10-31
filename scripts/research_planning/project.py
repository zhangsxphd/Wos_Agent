"""Create a project, connect the existing WoS search, and maintain traceable evidence."""
import argparse
import json
import sqlite3
from pathlib import Path

from scripts.merge_searches import merge_searches
from scripts.pipeline_utils import ROOT, read_jsonl, utc_now
from scripts.run_search_plan import run_plan

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
 source_type TEXT NOT NULL, access TEXT NOT NULL, notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 claim TEXT NOT NULL, location TEXT NOT NULL, evidence_type TEXT NOT NULL,
 treatment TEXT NOT NULL, control_group TEXT NOT NULL, experimental_unit TEXT NOT NULL,
 replicates TEXT NOT NULL, soil TEXT NOT NULL, measurement TEXT NOT NULL,
 result TEXT NOT NULL, significance TEXT NOT NULL, limitations TEXT NOT NULL,
 CHECK(evidence_type IN ('reported_fact','supported_inference','unverified_hypothesis'))
);
"""

def initialize(project, topic):
    project = Path(project).resolve()
    project.mkdir(parents=True, exist_ok=False)
    (project / 'project.json').write_text(json.dumps({
        'topic': topic, 'created_at': utc_now(), 'status': 'incomplete',
        'resources': {key: 'unknown' for key in ('equipment', 'budget', 'site', 'skills', 'timeline', 'borrow_or_outsource')},
        'limitations': ['主题解析、筛选、证据核对及决策仍需完成；现有文献池不是完整范围。']
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with sqlite3.connect(project / 'evidence.sqlite') as db:
        db.executescript(SCHEMA)
    (project / 'README.md').write_text(
        '# 研究项目\n\n主题：' + topic + '\n\n状态：未完成。先制定并执行检索计划，核实来源和资源，再填写分析与评审。\n', encoding='utf-8')
    return project

def local_search(project, input_path, terms):
    project = Path(project).resolve()
    if not (project / 'project.json').is_file():
        raise ValueError('Not a research project')
    if (project / 'local_candidates.jsonl').exists() or (project / 'local_search.json').exists():
        raise FileExistsError('Local search outputs exist; use a new project or archive the prior run')
    terms = [x.strip().casefold() for x in terms if x.strip()]
    if not terms:
        raise ValueError('At least one local search term is required')
    records = read_jsonl(input_path)
    matches = []
    for row in records:
        haystack = ' '.join(str(row.get(k) or '') for k in ('title', 'abstract', 'author_keywords')).casefold()
        found = [term for term in terms if term in haystack]
        if found:
            matches.append({'record': row, 'matched_terms': found, 'screening_status': 'unreviewed'})
    output = project / 'local_candidates.jsonl'
    with output.open('x', encoding='utf-8') as f:
        for item in matches:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    manifest = {'input': str(Path(input_path).resolve()), 'terms_any': terms, 'input_count': len(records),
                'candidate_count': len(matches), 'searched_at': utc_now(),
                'limitations': '关键词召回不等于纳入筛选；该文件不是完整文献范围。'}
    (project / 'local_search.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest

def search(project, plan, max_records=None, max_pages=None):
    project = Path(project).resolve()
    if not (project / 'project.json').is_file():
        raise ValueError('Not a research project')
    path = project / 'wos_search_manifest.json'
    if path.exists() or (project / 'wos_records.jsonl').exists():
        raise FileExistsError('WoS search outputs exist; use a new project or archive the prior run')
    manifest = run_plan(plan, root=ROOT, max_records=max_records, max_pages=max_pages)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    files = [ROOT / q['processed_file'] for q in manifest['queries'] if (ROOT / q['processed_file']).is_file()]
    if files:
        merge_searches(files, project / 'wos_records.jsonl', root=ROOT)
    return manifest

def import_evidence(project, payload):
    data = json.loads(Path(payload).read_text(encoding='utf-8'))
    sources, observations = data['sources'], data['observations']
    source_fields = ('id','title','url','source_type','access','notes')
    observation_fields = ('id','source_id','claim','location','evidence_type','treatment','control_group',
                          'experimental_unit','replicates','soil','measurement','result','significance','limitations')
    for row in sources:
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in source_fields):
            raise ValueError('Every source field must be a nonempty string')
        if not row['url'].startswith(('https://','http://')):
            raise ValueError('Source URL must be HTTP(S)')
    for row in observations:
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in observation_fields):
            raise ValueError('Every observation field must be a nonempty string; use unknown when needed')
        if row['evidence_type'] not in ('reported_fact','supported_inference','unverified_hypothesis'):
            raise ValueError('Invalid evidence type')
    db_path = Path(project) / 'evidence.sqlite'
    if not db_path.is_file():
        raise ValueError('Project evidence database not found')
    with sqlite3.connect(db_path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executemany('INSERT INTO sources VALUES (?,?,?,?,?,?)', [tuple(row[k] for k in source_fields) for row in sources])
        db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [tuple(row[k] for k in observation_fields) for row in observations])
    return len(sources), len(observations)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('init'); p.add_argument('project'); p.add_argument('--topic', required=True)
    p = sub.add_parser('local'); p.add_argument('project'); p.add_argument('--input', required=True); p.add_argument('--terms', nargs='+', required=True)
    p = sub.add_parser('search'); p.add_argument('project'); p.add_argument('--plan', required=True); p.add_argument('--max-records', type=int); p.add_argument('--max-pages', type=int)
    p = sub.add_parser('import-evidence'); p.add_argument('project'); p.add_argument('payload')
    args = parser.parse_args()
    try:
        if args.action == 'init': print(initialize(args.project, args.topic))
        elif args.action == 'local': print(json.dumps(local_search(args.project, args.input, args.terms), ensure_ascii=False))
        elif args.action == 'search':
            result = search(args.project, args.plan, args.max_records, args.max_pages)
            return 0 if result['status'] == 'completed' else 1
        else: print('sources, observations:', import_evidence(args.project, args.payload))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')

if __name__ == '__main__':
    raise SystemExit(main())
