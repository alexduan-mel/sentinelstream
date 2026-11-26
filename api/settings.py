"""Settings the web UI needs before it can render: for now, the display time zone."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from alerts.display_time import DisplayTimezoneError, display_timezone

router = APIRouter(prefix="/api", tags=["settings"])


class Settings(BaseModel):
    # IANA zone name the UI shows times in, e.g. America/New_York.
    display_timezone: str


@router.get("/settings", response_model=Settings)
def get_settings() -> Settings:
    try:
        zone = display_timezone()
    except DisplayTimezoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return Settings(display_timezone=zone.key)
