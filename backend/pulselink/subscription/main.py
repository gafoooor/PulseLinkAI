"""FastAPI entrypoint for the Subscription Generator.

Scaffold only — slot generation and donor matching are implemented in task 8.
Run locally with:

    uvicorn pulselink.subscription.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="PulseLink Subscription Generator", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "subscription", "status": "ok"}
