"""FastAPI entrypoint for the LLM Parsing Service.

Scaffold only — parsing logic and the pluggable ``LlmClient`` are implemented
in task 3. Run locally with:

    uvicorn pulselink.parsing.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="PulseLink Parsing Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "parsing", "status": "ok"}
