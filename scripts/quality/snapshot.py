"""Bind a quality snapshot to inputs, validation code and observed files."""
import hashlib
import importlib.metadata
import platform
from pathlib import Path

from scripts.fulltext.admission import project_file


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context():
    base=Path(__file__).resolve().parents[2]
    files=('scripts/quality/audit.py','scripts/quality/snapshot.py','scripts/fulltext/admission.py',
           'scripts/fulltext/pdf_metadata.py','scripts/fulltext/resolver.py','scripts/fulltext/tei_parser.py')
    packages={}
    for name in ('pypdf','fonttools','pdfplumber','pdfminer.six'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    return {'python':platform.python_version(),'packages':packages,
            'code_sha256':{name:digest(base/name) for name in files}}


def fingerprints(root,row):
    values={}
    for field in ('raw_file','parsed_file'):
        value=row.get(field)
        if not value:values[field]=None;continue
        try:values[field]=digest(project_file(root,value))
        except (ValueError,OSError):values[field]='unreadable_or_unsafe'
    return values


def verify_snapshot(snapshot,rows_path,canonical,inventory,rows,root):
    info=snapshot.get('snapshot_integrity') or {}
    if info.get('version')!='quality-snapshot-v2':raise ValueError('snapshot_unbound_reaudit_required')
    if digest(rows_path)!=info.get('quality_rows_sha256'):raise ValueError('quality_rows_hash_mismatch')
    inputs=snapshot.get('inputs') or {}
    if digest(canonical)!=inputs.get('canonical_sha256'):raise ValueError('canonical_hash_mismatch')
    if not inventory or digest(inventory)!=inputs.get('inventory_sha256'):raise ValueError('inventory_hash_mismatch')
    if info.get('validation_context')!=context():raise ValueError('snapshot_validation_context_changed')
    for row in rows:
        if 'observed_file_sha256' not in row:raise ValueError('snapshot_file_fingerprints_missing')
        if fingerprints(root,row)!=row['observed_file_sha256']:
            raise ValueError('snapshot_source_changed:'+str(row.get('uid')))
    return True
