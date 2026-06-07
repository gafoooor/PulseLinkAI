"""FastAPI entrypoint demonstrating the RBAC seam (Task 13.3).

This is a small *example* gateway showing how the reusable
``pulselink.gateway.rbac`` checks plug into a FastAPI route via the
:func:`get_principal` dependency. The five core services keep their own
endpoints; wiring RBAC into each of them is their own concern. Run locally:

    uvicorn pulselink.gateway.main:app --reload

PRODUCTION: this maps to Amazon API Gateway + Lambda authorizers + IAM — the
header-based ``Principal`` below is replaced by the authorizer's request
context, while the authorization rules stay identical.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException

from pulselink.gateway.rbac import (
    AccessDenied,
    Principal,
    Role,
    get_principal,
)

app = FastAPI(title="PulseLink API Gateway (RBAC demo)", version="0.1.0")


def principal_dependency(
    x_role: Optional[str] = Header(default=None),
    x_subject_id: Optional[str] = Header(default=None),
    x_city_id: Optional[str] = Header(default=None),
) -> Principal:
    """FastAPI dependency: resolve the caller, surfacing denials as HTTP 403."""

    try:
        return get_principal(x_role, x_subject_id, x_city_id)
    except AccessDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "gateway", "status": "ok"}


@app.get("/whoami")
def whoami(principal: Principal = Depends(principal_dependency)) -> dict[str, object]:
    """Example guarded route: echoes the authenticated principal.

    Only coordinators carry a city scope here; the route simply demonstrates a
    successfully authenticated principal flowing in from the gateway seam.
    """

    return {
        "role": principal.role.value,
        "subject_id": principal.subject_id,
        "city_id": principal.city_id,
        "is_coordinator": principal.role is Role.COORDINATOR,
    }
