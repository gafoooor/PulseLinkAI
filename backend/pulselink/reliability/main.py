"""FastAPI entrypoint for the Donor Reliability Service.

Scaffold only — scoring and tiering are implemented in task 6. Run locally with:

    uvicorn pulselink.reliability.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="PulseLink Reliability Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "reliability", "status": "ok"}
