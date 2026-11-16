from pydantic import BaseModel, Field


class ShareCreateInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    job_id: int
    description: str = Field(default='', max_length=1000)
    expires_in_seconds: int | None = Field(default=None, ge=1, le=2592000)
    expires_in_minutes: float | None = Field(default=None, gt=0, le=43200)
    expires_in_hours: float | None = Field(default=None, gt=0, le=720)
    idempotency_key: str | None = Field(default=None, max_length=100)


class ShareClaimInput(BaseModel):
    token: str = Field(min_length=8, max_length=200)
