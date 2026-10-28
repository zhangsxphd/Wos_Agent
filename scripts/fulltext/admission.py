"""Revalidate file identity and parsed text before using fulltext evidence.

The decision is recomputed from the current raw and parsed bytes. No manifest
flag or filename is treated as proof. Technical checks do not constitute
human review or guarantee correct scientific interpretation.
"""
import hashlib
import io
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .pdf_metadata import parse_pdf,parse_pdfplumber
from .tei_parser import parse_tei, tei_identity
from .provenance import FulltextError
from ..extractors.schema_validator import EvidenceValidationError
from ..providers.base_client import canonical_doi


def project_file(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise EvidenceValidationError('unsafe_fulltext_path')
    root = Path(root).resolve()
    path = (root / name).resolve()
    try: path.relative_to(root)
    except ValueError: raise EvidenceValidationError('unsafe_fulltext_path') from None
    if not path.is_file(): raise EvidenceValidationError('fulltext_file_missing')
    return path


def inspect_fulltext(record, manifest, root):
    """Return a fresh parsed snapshot and technical admission details or fail closed."""
    if not isinstance(manifest, dict) or manifest.get('status') != 'available':
        raise EvidenceValidationError('fulltext_not_available')
    if not record.get('uid') or record.get('uid') != manifest.get('uid'):
        raise EvidenceValidationError('fulltext_uid_mismatch')
    try:
        if canonical_doi(record.get('doi')) != canonical_doi(manifest.get('doi')):
            raise EvidenceValidationError('fulltext_doi_mismatch')
    except (ValueError, TypeError, AttributeError):
        raise EvidenceValidationError('fulltext_doi_mismatch') from None
    raw_path = project_file(root, manifest.get('raw_file'))
    parsed_path = project_file(root, manifest.get('parsed_file'))
    raw = raw_path.read_bytes(); saved_bytes = parsed_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not manifest.get('sha256') or digest != manifest['sha256']:
        raise EvidenceValidationError('fulltext_raw_hash_mismatch')
    try:
        saved = json.loads(saved_bytes)
        if not isinstance(saved, dict) or not isinstance(saved.get('parser'), str):
            raise EvidenceValidationError('parsed_parser_missing')
        if manifest.get('parsed_sha256') and hashlib.sha256(saved_bytes).hexdigest() != manifest['parsed_sha256']:
            raise EvidenceValidationError('parsed_hash_mismatch')
        if manifest.get('parser') and manifest['parser'] != saved['parser']:
            raise EvidenceValidationError('parsed_parser_mismatch')
        if manifest.get('format') == 'tei_xml':
            identity = tei_identity(raw, record['doi'])
            if identity['status'] != 'doi_match':
                raise EvidenceValidationError('tei_identity_' + identity['status'])
            fresh = parse_tei(raw)
        elif manifest.get('format') == 'pdf':
            from .resolver import verify_pdf_identity
            from pypdf import PdfReader
            if not raw.lstrip().startswith(b'%PDF-') or b'%%EOF' not in raw[-4096:]:
                raise EvidenceValidationError('pdf_header_or_eof_missing')
            try:
                reader=PdfReader(io.BytesIO(raw),strict=True)
                if reader.is_encrypted or not reader.pages:
                    raise EvidenceValidationError('pdf_unreadable_or_empty')
            except EvidenceValidationError: raise
            except Exception: raise EvidenceValidationError('pdf_strict_parse_failed') from None
            fresh = parse_pdf(raw)
            verify_pdf_identity(fresh, record)
            identity = {'status': 'doi_match', 'method': 'doi_on_first_page'}
            if saved['parser'] == 'pypdf-page-v2-fonttools':
                environment = {'pypdf': version('pypdf'), 'fonttools': version('fonttools')}
                if saved.get('parser_environment') != environment:
                    raise EvidenceValidationError('parser_environment_changed')
                fresh['parser'] = saved['parser']; fresh['parser_environment'] = environment
            elif saved['parser'] == 'pdfplumber-page-v2':
                fresh = parse_pdfplumber(raw)
        else:
            raise EvidenceValidationError('unsupported_fulltext_format')
        if saved != fresh:
            raise EvidenceValidationError('parsed_content_not_reproduced')
        if not fresh.get('body_paragraph_count'):
            raise EvidenceValidationError('fulltext_no_body_text')
    except FulltextError as exc:
        raise EvidenceValidationError(exc.code) from None
    except (PackageNotFoundError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
        raise EvidenceValidationError('parsed_content_invalid') from None
    # Detect replacement during the check; downstream uses this in-memory snapshot.
    parsed_hash = hashlib.sha256(saved_bytes).hexdigest()
    if hashlib.sha256(raw_path.read_bytes()).hexdigest() != digest or hashlib.sha256(parsed_path.read_bytes()).hexdigest() != parsed_hash:
        raise EvidenceValidationError('fulltext_changed_during_validation')
    return fresh, {'admission': 'technical_pass', 'identity': identity,
                   'raw_sha256': digest, 'parsed_sha256': parsed_hash,
                   'parser': fresh['parser'], 'human_review_status': 'not_assessed'}


def validate_record_evidence(record, row, root):
    """Reject unsupported fulltext layers; allow a genuinely empty layer."""
    from .evidence import empty_fulltext, validate_fulltext
    if 'fulltext_evidence' not in row:
        return None
    evidence = row['fulltext_evidence']
    if evidence == empty_fulltext():
        validate_fulltext(evidence, {'sections': []}, '')
        return None
    if row.get('uid') is not None and row.get('uid') != record.get('uid'):
        raise EvidenceValidationError('evidence_uid_mismatch')
    if row.get('doi') is not None:
        try:
            if canonical_doi(row['doi']) != canonical_doi(record.get('doi')):
                raise EvidenceValidationError('evidence_doi_mismatch')
        except (ValueError, TypeError, AttributeError):
            raise EvidenceValidationError('evidence_doi_mismatch') from None
    parsed, admission = inspect_fulltext(record, row.get('fulltext_manifest'), root)
    validate_fulltext(evidence, parsed, admission['raw_sha256'])
    return admission
