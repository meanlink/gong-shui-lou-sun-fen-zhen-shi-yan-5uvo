"""Time-limited, de-identified sharing of an approved analysis result.

The internal job and dataset are never exposed by the public endpoints.  A
package stores a frozen copy of the input records and result at build time,
plus one stable pseudonym per original sample reference.  Pseudonyms are
derived with a package-specific secret, so the same sample cannot be linked
across two packages.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
from copy import deepcopy

from cryptography.fernet import Fernet
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from src.config.database import Session
from src.config.settings import ARTIFACT_DIR, JWT_SECRET
from src.models import Dataset, Job, ShareCode, SharePackage
from src.services.audit import record
from src.services.business import FEATURES, LABELS

DEFAULT_TTL = 7 * 24 * 3600
MAX_TTL = 30 * 24 * 3600
BUILD_LEASE_SECONDS = 60
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


# ---------------------------------------------------------------- credentials
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(('share-package:' + JWT_SECRET).encode()).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


def new_credentials():
    token = secrets.token_urlsafe(32)
    return {
        'token': token,
        'token_hash': token_hash(token),
        'token_hint': token[:6] + '…',
        'token_cipher': encrypt_token(token),
        'salt': secrets.token_hex(32),
        'public_id': secrets.token_hex(8),
    }


# ------------------------------------------------------------------- status
def effective_status(package: SharePackage, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if package.revoked_at:
        return 'revoked'
    if package.status == 'expired' or now >= package.expires_at:
        return 'expired'
    return package.status


def ttl_seconds(data) -> int:
    if data.expires_in_seconds is not None:
        value = int(data.expires_in_seconds)
    elif data.expires_in_minutes is not None:
        value = int(round(data.expires_in_minutes * 60))
    elif data.expires_in_hours is not None:
        value = int(round(data.expires_in_hours * 3600))
    else:
        value = DEFAULT_TTL
    return max(1, min(MAX_TTL, value))


# ------------------------------------------------------------------ sanitize
def _reference_prefixes(refs) -> set[str]:
    prefixes = set()
    for ref in refs:
        match = re.match(r'^([A-Za-z]{1,8})[-_]?\d+$', str(ref))
        if match:
            prefixes.add(match.group(1))
    return prefixes


def sanitize_text(value, mapping: dict[str, str] | None = None):
    """Remove original identifiers from free text.

    Known references are substituted with their package code (or a generic
    label when no mapping exists).  Sibling identifiers sharing the same
    prefix (for example a guessed ``NC-999``) are redacted as well.  Package
    codes are protected with sentinels so they are never re-redacted.
    """
    if not isinstance(value, str) or not value:
        return value
    mapping = mapping or {}
    sentinels: dict[str, str] = {}

    refs = sorted((str(r) for r in mapping if r), key=len, reverse=True)
    if refs:
        boundary = r'(?<![A-Za-z0-9])'
        pattern = re.compile(boundary + '(' + '|'.join(re.escape(r) for r in refs) + r')(?![A-Za-z0-9])', re.IGNORECASE)
        lowered = {str(r).lower(): c for r, c in mapping.items()}

        def substitute(match):
            code = lowered.get(match.group(0).lower())
            if not code:
                return '样本'
            marker = f'\u0001{len(sentinels)}\u0002'
            sentinels[marker] = code
            return marker

        value = pattern.sub(substitute, value)

    prefixes = _reference_prefixes(mapping)
    if prefixes:
        # Known references were substituted above; redact any remaini
        # guessable sibling (NC-999, NC-77a, NC001, ...).  A leading letter
        # before the prefix is a different identifier (ANC-001) and is kept.
        pattern = re.compile(
            '(?i)(?<![A-Za-z])(?:' + '|'.join(re.escape(p) for p in sorted(prefixes)) + r')(?:[-_ ]?[0-9]+(?:[A-Za-z]+)?)(?![0-9A-Za-z])'
        )
        value = pattern.sub('样本', value)

    for marker, code in sentinels.items():
        value = value.replace(marker, code)
    return value


def sanitize_obj(value, mapping):
    if isinstance(value, str):
        return sanitize_text(value, mapping)
    if isinstance(value, list):
        return [sanitize_obj(item, mapping) for item in value]
    if isinstance(value, dict):
        return {sanitize_text(str(k), mapping) if isinstance(k, str) else k: sanitize_obj(v, mapping) for k, v in value.items()}
    return value


# ------------------------------------------------------------------ pseudonym
def derive_code(salt: str, ref: str, attempt: int = 0) -> str:
    digest = hmac.new(salt.encode(), f'{ref}|{attempt}'.encode(), hashlib.sha256).digest()
    number = int.from_bytes(digest, 'big')
    chars = []
    for _ in range(6):
        number, remainder = divmod(number, len(CODE_ALPHABET))
        chars.append(CODE_ALPHABET[remainder])
    return 'S-' + ''.join(chars)


def _ordered_refs(records, result) -> list[str]:
    refs, seen = [], set()
    for row in records or []:
        ref = str(row.get('segment_id', '')).strip()
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    for row in ((result or {}).get('table') or {}).get('rows') or []:
        ref = str(row.get('segment_id', '')).strip()
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def ensure_codes(db, package: SharePackage, refs: list[str]) -> dict[str, str]:
    """Create missing pseudonyms in small durable batches.

    Existing rows are always reused, which is what makes a restarted build
    keep the same code for an already-seen sample.  The loop reloads after a
    concurrent insert instead of trusting a stale in-memory map.
    """
    for _ in range(50):
        rows = list(db.scalars(select(ShareCode).where(ShareCode.package_id == package.id)))
        existing = {row.original_ref: row.code for row in rows}
        missing = [ref for ref in refs if ref not in existing]
        if not missing:
            return existing
        used = set(existing.values())
        for ref in missing[:40]:
            attempt = 0
            code = derive_code(package.salt, ref, attempt)
            while code in used:
                attempt += 1
                code = derive_code(package.salt, ref, attempt)
            used.add(code)
            db.add(ShareCode(package_id=package.id, original_ref=ref, code=code))
            existing[ref] = code
        try:
            db.commit()
        except IntegrityError:
            # Another builder inserted a batch first.  Reload and reuse its
            # codes for this package before generating anything else.
            db.rollback()
            continue
    raise RuntimeError('共享代号生成发生竞争，请重新接续制包')


# --------------------------------------------------------------- package data
def _input_fingerprint(package: SharePackage, dataset_checksum: str) -> str:
    return hmac.new(package.salt.encode(), ('input:' + dataset_checksum).encode(), hashlib.sha256).hexdigest()[:16]


def _samples(result, records, mapping):
    by_ref = {}
    for record in records or []:
        by_ref.setdefault(str(record.get('segment_id', '')).strip(), record)
    samples = []
    for row in ((result or {}).get('table') or {}).get('rows') or []:
        ref = str(row.get('segment_id', '')).strip()
        code = mapping.get(ref)
        if not code:
            continue
        sample = {
            'sample_code': code,
            'risk_pct': row.get('risk_pct'),
            'triage': sanitize_text(row.get('triage') or '', mapping),
            'actual': sanitize_text(row.get('actual') or '', mapping),
        }
        record = by_ref.get(ref)
        if record:
            for key in FEATURES:
                sample[key] = record.get(key)
        samples.append(sample)
    return samples


def build_payload(package: SharePackage, job: Job, dataset: Dataset, mapping: dict[str, str]) -> dict:
    result = job.result or {}
    records = dataset.records or []
    payload = {
        'public_id': package.public_id,
        'name': sanitize_text(package.name, mapping),
        'description': sanitize_text(package.description, mapping),
        'status': 'ready',
        'created_at': package.created_at,
        'ready_at': time.time(),
        'expires_at': package.expires_at,
        'summary': sanitize_text(result.get('summary') or '', mapping),
        'metrics': sanitize_obj(result.get('metrics') or [], mapping),
        'findings': sanitize_obj(result.get('findings') or [], mapping),
        'method': sanitize_text(result.get('method') or '', mapping),
        'warnings': sanitize_obj(result.get('warnings') or [], mapping),
        'params': sanitize_obj(deepcopy(job.params or {}), mapping),
        'chart': sanitize_obj(deepcopy(result.get('chart') or {}), mapping),
        'samples': _samples(result, records, mapping),
        'input_fingerprint': _input_fingerprint(package, dataset.checksum),
        'pseudonym_note': '同包内样本代号保持一致，可用于对照分诊结果；不同共享包的代号彼此独立，不能串联。',
    }
    payload['sample_count'] = len(payload['samples'])
    return payload


CSV_COLUMNS = [
    ('sample_code', '样本代号'),
    (FEATURES[0], LABELS[0]),
    (FEATURES[1], LABELS[1]),
    (FEATURES[2], LABELS[2]),
    (FEATURES[3], LABELS[3]),
    ('risk_pct', '叶节点漏点率(%)'),
    ('triage', '分诊建议'),
    ('actual', '留出集复核结果'),
]


def _csv_safe(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def build_csv(payload: dict) -> bytes:
    out = io.StringIO(newline='')
    out.write('﻿')
    writer = csv.writer(out)
    writer.writerow([label for _, label in CSV_COLUMNS])
    for row in payload.get('samples', []):
        writer.writerow([_csv_safe(row.get(key, '')) for key, _ in CSV_COLUMNS])
    return out.getvalue().encode('utf-8')


def canonical_checksum(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------- build
def _write_atomic(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp-' + secrets.token_hex(6)
    try:
        with open(temporary, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _lock_row(db, model, ident):
    """Best-effort SELECT ... FOR UPDATE (no-op on SQLite)."""
    try:
        return db.scalar(select(model).where(model.id == ident).with_for_update())
    except Exception:
        db.rollback()
        return db.get(model, ident)


def build(package_id: int, force: bool = False):
    """Build (or resume building) a package in the background/request.

    Finalisation is one conditional UPDATE.  A revocation or expiry that
    happens while the artifacts are being written therefore wins, and a late
    build can never turn the package back into a claimable one.
    """
    with Session() as db:
        package = db.get(SharePackage, package_id)
        if package is None:
            return None
        now = time.time()
        if package.status != 'building' or package.revoked_at or now >= package.expires_at:
            return package

        lease = now + BUILD_LEASE_SECONDS
        conditions = [
            SharePackage.id == package_id,
            SharePackage.status == 'building',
            SharePackage.revoked_at.is_(None),
            SharePackage.expires_at > now,
        ]
        if not force:
            conditions.append(or_(SharePackage.build_lease.is_(None), SharePackage.build_lease < now))
        claimed = db.execute(update(SharePackage).where(*conditions).values(build_lease=lease))
        db.commit()
        if claimed.rowcount != 1:
            return db.get(SharePackage, package_id, populate_existing=True)

        try:
            package = _lock_row(db, SharePackage, package_id)
            job = db.get(Job, package.job_id)
            if job is None or job.status != 'completed' or not job.approved:
                raise ValueError('来源试验不再满足已批准状态，无法制包')
            dataset = db.get(Dataset, job.dataset_id)
            if dataset is None:
                raise ValueError('来源数据批次不存在，无法制包')

            records = deepcopy(dataset.records or [])
            result = deepcopy(job.result or {})
            params = deepcopy(job.params or {})
            refs = _ordered_refs(records, result)
            mapping = ensure_codes(db, package, refs)
            payload = build_payload(package, job, dataset, mapping)
            checksum = canonical_checksum(payload)
            payload['content_checksum'] = checksum
            json_bytes = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
            csv_bytes = build_csv(payload)

            stamp = secrets.token_hex(4)
            csv_name = f'share-{package.public_id}-{stamp}-samples.csv'
            json_name = f'share-{package.public_id}-{stamp}-samples.json'
            _write_atomic(os.path.join(ARTIFACT_DIR, csv_name), csv_bytes)
            _write_atomic(os.path.join(ARTIFACT_DIR, json_name), json_bytes)

            snapshot = {
                'locked_at': time.time(),
                'inputs': {'dataset_checksum': dataset.checksum, 'records': records},
                'params': params,
                'result': result,
                'public_payload': {**payload, 'content_checksum': checksum},
                'pseudonyms': mapping,
            }
            finished_at = time.time()
            finalised = db.execute(
                update(SharePackage)
                .where(
                    SharePackage.id == package_id,
                    SharePackage.status == 'building',
                    SharePackage.revoked_at.is_(None),
                    SharePackage.expires_at > time.time(),
                    SharePackage.build_lease == lease,
                )
                .values(
                    status='ready',
                    ready_at=finished_at,
                    snapshot=snapshot,
                    item_count=len(payload['samples']),
                    content_checksum=checksum,
                    artifact_csv=csv_name,
                    artifact_json=json_name,
                    build_lease=None,
                    build_error='',
                    built_count=SharePackage.built_count + 1,
                )
            )
            db.commit()
            if finalised.rowcount != 1:
                # The conditional UPDATE above is the commit point.  Anything
                # that lost the race (revoke, expiry, another builder) must not
                # leave a complete file behind.  Each attempt writes under its
                # own stamp, so cleanup can never touch a winner's artifacts.
                current = db.get(SharePackage, package_id, populate_existing=True)
                db.refresh(current)
                if current.status in ('revoked', 'expired'):
                    for name in (csv_name, json_name):
                        path = os.path.join(ARTIFACT_DIR, name)
                        if os.path.exists(path):
                            os.unlink(path)
                elif current.status == 'building' and time.time() >= current.expires_at and not current.revoked_at:
                    current.status = 'expired'
                    db.commit()
                for name in (csv_name, json_name):
                    path = os.path.join(os.path.realpath(ARTIFACT_DIR), name)
                    if os.path.isfile(path):
                        try:
                            os.unlink(path)
                        except OSError:
                            logging.exception('Could not remove losing share artifact')
                return current
            record(db, 'worker', 'share.built', {'id': package_id, 'samples': len(payload['samples']), 'checksum': checksum})
            db.commit()
            return db.get(SharePackage, package_id, populate_existing=True)
        except Exception as exc:  # keep partial pseudonyms for a later resume
            logging.exception('Share package build failed')
            db.rollback()
            try:
                db.execute(
                    update(SharePackage)
                    .where(SharePackage.id == package_id)
                    .values(build_lease=time.time() + 15, build_error=str(exc)[:990])
                )
                db.commit()
            except Exception:
                db.rollback()
            return db.get(SharePackage, package_id, populate_existing=True)


# -------------------------------------------------------------------- public
def public_content(package: SharePackage, now: float | None = None) -> dict:
    payload = deepcopy((package.snapshot or {}).get('public_payload') or {})
    now = time.time() if now is None else now
    payload['status'] = effective_status(package, now)
    payload['expires_at'] = package.expires_at
    payload['remaining_seconds'] = max(0, int(package.expires_at - now))
    payload['claim_count'] = package.claim_count or 0
    return payload


def find_by_token(db, token: str):
    if not token:
        return None
    return db.scalar(select(SharePackage).where(SharePackage.token_hash == token_hash(token)))


def authorize(db, token: str, public_id: str | None = None):
    """Return the package only when the credential belongs to that package."""
    if not token:
        return None
    if public_id:
        package = db.scalar(select(SharePackage).where(SharePackage.public_id == public_id))
    else:
        package = find_by_token(db, token)
    if package is None:
        return None
    if not hmac.compare_digest(package.token_hash, token_hash(token)):
        return None
    return package


def purge_artifacts(package: SharePackage):
    """Remove delivered copies once a package can no longer be claimed."""
    for name in (package.artifact_csv, package.artifact_json):
        if not name:
            continue
        path = os.path.join(os.path.realpath(ARTIFACT_DIR), name)
        if os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                logging.exception('Could not purge share artifact')


def artifact_path(package: SharePackage, format: str) -> str:
    name = package.artifact_json if format == 'json' else package.artifact_csv
    return os.path.join(ARTIFACT_DIR, name) if name else ''
