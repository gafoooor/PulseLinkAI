"""FastAPI entrypoint for the Notification / Messaging Service.

Scaffold only — the pluggable ``MessageChannel`` / ``VoiceChannel`` seam and
the offer/decline flow are implemented in tasks 9 and 10. Run locally with:

    uvicorn pulselink.messaging.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="PulseLink Messaging Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "messaging", "status": "ok"}
