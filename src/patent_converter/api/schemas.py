"""Public API schemas. Property names are part of the OutSystems contract: do not rename."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ClaimPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claimNumber: str
    claimAr: str
    claimEn: str


class ConvertResponse(BaseModel):
    """Successful conversion: exactly {"claims": [...]}."""
    model_config = ConfigDict(extra="forbid")
    claims: list[ClaimPair]


class ErrorDetail(BaseModel):
    code: str
    message: str
    requestId: str | None = None
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorDetail
