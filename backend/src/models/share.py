from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class SharePackage(Base):
    """A time-limited, de-identified snapshot of one approved analysis."""

    __tablename__ = 'share_packages'
    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(24), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(1000), default='')
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    salt: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_hint: Mapped[str] = mapped_column(String(16), default='')
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='building')
    expires_at: Mapped[float] = mapped_column(Double)
    created_at: Mapped[float] = mapped_column(Double)
    ready_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    revoked_by: Mapped[str] = mapped_column(String(150), default='')
    claim_count: Mapped[int] = mapped_column(Integer, default=0)
    last_claimed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    built_count: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    token_cipher: Mapped[str] = mapped_column(String(500), default='')
    build_lease: Mapped[float | None] = mapped_column(Double, nullable=True)
    build_error: Mapped[str] = mapped_column(String(1000), default='')
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    content_checksum: Mapped[str] = mapped_column(String(64), default='')
    artifact_csv: Mapped[str] = mapped_column(String(160), default='')
    artifact_json: Mapped[str] = mapped_column(String(160), default='')

    __table_args__ = (
        UniqueConstraint('created_by', 'idempotency_key', name='uq_share_package_idempotency'),
    )


class ShareCode(Base):
    """Stable package-scoped pseudonym for one original sample reference."""

    __tablename__ = 'share_codes'
    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey('share_packages.id'))
    original_ref: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(24))

    __table_args__ = (
        UniqueConstraint('package_id', 'original_ref', name='uq_share_code_original'),
        UniqueConstraint('package_id', 'code', name='uq_share_code_value'),
    )
