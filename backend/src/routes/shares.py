import os
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.config.database import get_db
from src.middlewares.auth import admin_only
from src.models import Job, SharePackage
from src.schemas.share import ShareClaimInput, ShareCreateInput
from src.services import shares
from src.services.audit import record

router = APIRouter()


# ------------------------------------------------------------------ helpers
def _owned(db, package_id: int, user):
    package = db.get(SharePackage, package_id)
    if package is None:
        raise HTTPException(404, '共享包不存在')
    if user.role != 'admin' and package.created_by != user.id:
        raise HTTPException(403, '需要业务负责人操作')
    return package


def admin_dict(package: SharePackage, reveal_token: bool = False) -> dict:
    now = time.time()
    status = shares.effective_status(package, now)
    data = {
        'id': package.id,
        'public_id': package.public_id,
        'name': package.name,
        'description': package.description,
        'job_id': package.job_id,
        'status': status,
        'expires_at': package.expires_at,
        'remaining_seconds': max(0, int(package.expires_at - now)),
        'created_at': package.created_at,
        'ready_at': package.ready_at,
        'revoked_at': package.revoked_at,
        'claim_count': package.claim_count or 0,
        'last_claimed_at': package.last_claimed_at,
        'built_count': package.built_count or 0,
        'item_count': package.item_count or 0,
        'download_count': package.download_count or 0,
        'content_checksum': package.content_checksum,
        'token_hint': package.token_hint,
        'build_error': package.build_error,
        'downloadable': status == 'ready',
        'scope_note': '同包样本代号保持一致；异包代号彼此独立，不能串联',
    }
    if reveal_token and package.token_cipher and status in ('building', 'ready'):
        data['credential'] = shares.decrypt_token(package.token_cipher)
        data['claim_url'] = '/shares/claim/' + package.public_id
    return data


def _expire_if_due(db, package: SharePackage) -> SharePackage:
    now = time.time()
    if not package.revoked_at and package.status in ('building', 'ready') and now >= package.expires_at:
        package.status = 'expired'
        db.commit()
        shares.purge_artifacts(package)
    return package


def _schedule_build(package_id: int):
    from src.services.jobs import enqueue_share
    enqueue_share(package_id)


# -------------------------------------------------------------------- admin
@router.get('/shares')
def list_shares(db=Depends(get_db), user=Depends(admin_only)):
    packages = list(db.scalars(select(SharePackage).order_by(SharePackage.id.desc()).limit(100)))
    for package in packages:
        _expire_if_due(db, package)
    return [admin_dict(p, reveal_token=True) for p in packages]


@router.post('/shares', status_code=201)
def create_share(data: ShareCreateInput, background: BackgroundTasks, db=Depends(get_db), user=Depends(admin_only)):
    key = (data.idempotency_key or '').strip() or None
    if key:
        existing = db.scalar(select(SharePackage).where(SharePackage.created_by == user.id, SharePackage.idempotency_key == key))
        if existing:
            _expire_if_due(db, existing)
            return {**admin_dict(existing, reveal_token=True), 'reused': True}
    job = db.get(Job, data.job_id)
    if job is None:
        raise HTTPException(404, '分析任务不存在')
    if job.status != 'completed' or not job.approved:
        raise HTTPException(409, '仅已完成且获批准的分析可以制包共享')
    now = time.time()
    credentials = shares.new_credentials()
    package = SharePackage(
        public_id=credentials['public_id'], name=data.name.strip(), description=(data.description or '').strip(),
        job_id=job.id, created_by=user.id, salt=credentials['salt'], token_hash=credentials['token_hash'],
        token_hint=credentials['token_hint'], token_cipher=credentials['token_cipher'], idempotency_key=key,
        status='building', expires_at=now + shares.ttl_seconds(data), created_at=now,
    )
    db.add(package)
    try:
        db.flush()
        record(db, user.email, 'share.create', {'id': package.id, 'job': job.id, 'expires_at': package.expires_at})
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SharePackage).where(SharePackage.created_by == user.id, SharePackage.idempotency_key == key))
        if existing is None:
            raise HTTPException(409, '共享包创建冲突，请重试')
        return {**admin_dict(existing, reveal_token=True), 'reused': True}
    _schedule_build(package.id)
    background.add_task(shares.build, package.id)
    return {**admin_dict(package, reveal_token=True), 'reused': False}


@router.get('/shares/{package_id}')
def share_detail(package_id: int, db=Depends(get_db), user=Depends(admin_only)):
    package = _owned(db, package_id, user)
    _expire_if_due(db, package)
    return admin_dict(package, reveal_token=True)


@router.post('/shares/{package_id}/resume')
def resume_share(package_id: int, background: BackgroundTasks, db=Depends(get_db), user=Depends(admin_only)):
    package = _owned(db, package_id, user)
    _expire_if_due(db, package)
    if package.status == 'expired':
        raise HTTPException(409, '共享包已过期，不能接续制包')
    if package.revoked_at:
        raise HTTPException(409, '共享包已撤销，不能接续制包')
    if package.status == 'ready':
        return admin_dict(package, reveal_token=True)
    package.build_error = ''
    db.commit()
    _schedule_build(package.id)
    background.add_task(shares.build, package.id, True)
    return admin_dict(package)


@router.post('/shares/{package_id}/revoke')
def revoke_share(package_id: int, db=Depends(get_db), user=Depends(admin_only)):
    package = _owned(db, package_id, user)
    now = time.time()
    revoked = db.execute(
        update(SharePackage)
        .where(SharePackage.id == package_id, SharePackage.revoked_at.is_(None))
        .values(status='revoked', revoked_at=now, revoked_by=user.email, build_lease=None)
    )
    if revoked.rowcount == 1:
        db.expire_all()
        package = db.get(SharePackage, package_id)
        shares.purge_artifacts(package)
        record(db, user.email, 'share.revoke', {'id': package_id})
    db.commit()
    package = db.get(SharePackage, package_id)
    return admin_dict(package)


@router.get('/shares/{package_id}/credential')
def share_credential(package_id: int, db=Depends(get_db), user=Depends(admin_only)):
    package = _owned(db, package_id, user)
    _expire_if_due(db, package)
    status = shares.effective_status(package)
    if status != 'ready':
        raise HTTPException(409, '共享包尚未就绪或已失效')
    return {'public_id': package.public_id, 'credential': shares.decrypt_token(package.token_cipher), 'expires_at': package.expires_at}


@router.get('/shares/{package_id}/download')
def admin_download(package_id: int, format: str = 'csv', db=Depends(get_db), user=Depends(admin_only)):
    package = _owned(db, package_id, user)
    _expire_if_due(db, package)
    if shares.effective_status(package) != 'ready':
        raise HTTPException(410, '共享包已过期或已撤销')
    return _artifact_response(package, format)


# ------------------------------------------------------------------- public
@router.post('/shares/claim/{public_id}')
def claim(public_id: str, data: ShareClaimInput, db=Depends(get_db)):
    package = shares.authorize(db, data.token, public_id)
    if package is None:
        raise HTTPException(404, '共享包不存在或凭据无效')
    now = time.time()
    if package.revoked_at:
        raise HTTPException(410, '共享包已撤销')
    if now >= package.expires_at:
        _expire_if_due(db, package)
        raise HTTPException(410, '共享包已过期')
    if package.status != 'ready':
        raise HTTPException(409, '共享包尚未备齐，请稍后再试')
    claimed = db.execute(
        update(SharePackage)
        .where(
            SharePackage.id == package.id,
            SharePackage.status == 'ready',
            SharePackage.revoked_at.is_(None),
            SharePackage.expires_at > now,
        )
        .values(claim_count=SharePackage.claim_count + 1, last_claimed_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(410, '共享包已过期或已撤销')
    record(db, 'external', 'share.claim', {'id': package.id, 'public_id': package.public_id})
    db.commit()
    package = db.get(SharePackage, package.id)
    return shares.public_content(package, now)


@router.get('/shares/claim/{public_id}/download')
def claim_download(public_id: str, format: str = 'csv', token: str | None = Header(default=None, alias='X-Share-Token'), authorization: str | None = Header(default=None), db=Depends(get_db)):
    credential = token or ''
    if not credential and authorization and authorization.lower().startswith('bearer '):
        credential = authorization[7:].strip()
    package = shares.authorize(db, credential, public_id)
    if package is None:
        raise HTTPException(404, '共享包不存在或凭据无效')
    now = time.time()
    if package.revoked_at:
        raise HTTPException(410, '共享包已撤销')
    if now >= package.expires_at:
        _expire_if_due(db, package)
        raise HTTPException(410, '共享包已过期')
    if package.status != 'ready':
        raise HTTPException(409, '共享包尚未备齐，请稍后再试')
    response = _artifact_response(package, format)
    counted = db.execute(
        update(SharePackage)
        .where(
            SharePackage.id == package.id,
            SharePackage.status == 'ready',
            SharePackage.revoked_at.is_(None),
            SharePackage.expires_at > time.time(),
        )
        .values(download_count=SharePackage.download_count + 1)
    )
    if counted.rowcount != 1:
        db.rollback()
        raise HTTPException(410, '共享包已过期或已撤销')
    record(db, 'external', 'share.download', {'id': package.id, 'public_id': package.public_id, 'format': format})
    db.commit()
    return response


def _artifact_response(package: SharePackage, format: str):
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    directory = os.path.realpath(shares.ARTIFACT_DIR)
    name = package.artifact_json if format == 'json' else package.artifact_csv
    path = os.path.realpath(os.path.join(directory, name)) if name else ''
    if not name or not path.startswith(directory + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, '共享文件尚未生成')
    with open(path, 'rb') as handle:
        content = handle.read()
    filename = 'share-' + package.public_id + '-samples.' + format
    return Response(
        content=content,
        media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="' + filename + '"'},
    )
