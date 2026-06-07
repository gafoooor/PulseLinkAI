"""FastAPI entrypoint for the Forecasting Engine.

Scaffold only — EWMA cadence estimation and re-learning are implemented in
task 4. Run locally with:

    uvicorn pulselink.forecasting.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="PulseLink Forecasting Engine", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "forecasting", "status": "ok"}
