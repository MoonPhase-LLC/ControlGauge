"""User-registered external endpoints (Sprint 6, S6-05).

    POST   /model-endpoints               register a LOOPBACK endpoint (probe-gated)
    GET    /model-endpoints               structural list (recorded health, no probes)
    DELETE /model-endpoints/{id}          unregister (refused while attached)
    POST   /model-endpoints/{id}/health   live bounded check, persists last_health_*
    POST   /model-endpoints/{id}/attach   make the endpoint a role's occupant

An external endpoint is a local OpenAI-compatible inference server the
user is already running (llama-server qualifies). Registration is
fail-closed: the base URL must be loopback (`_validate_loopback_base_url`
-- non-loopback refused, tested), and the endpoint must pass a bounded
real health probe for EVERY declared capability before a row is
persisted -- an endpoint we cannot verify is not registered.

The stored URL is normalized to ``scheme://host:port``: no userinfo, no
path, no query. A path could carry sensitive text, so one is refused at
registration rather than stored and echoed (T-10). **No credential
column exists** -- V0.1 registers unauthenticated loopback endpoints
only; an endpoint requiring Bearer auth fails its probe with
``auth_rejected`` (a documented seam, `docs/OPEN_QUESTIONS.md`).

Detach is the existing role stop route (`POST /models/{id}/stop` stops
the role's occupant): the manager's ``stop(role)`` calls the provider's
idempotent ``stop()``, which closes our client -- the endpoint itself
keeps running for its owner.

Errors use the fixed contract in `routes/errors.py`; registry failures
surface the `REGISTRY_ERROR_CODES` code with its fixed message. All
handlers are plain ``def`` (the `routes/models.py` threadpool pattern):
the health probe is multi-second blocking work.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictStr
from sqlalchemy import select
from starlette.responses import JSONResponse

from ..db import Database
from ..db.models import ExternalEndpoint
from ..evidence.filetype import DISALLOWED_NAME_CHARS
from ..model_runtime.errors import ModelRuntimeError
from ..model_runtime.external import ExternalEndpointProvider, endpoint_health_state
from ..model_runtime.manager import ModelRuntimeManager
from ..model_runtime.protocols import Role
from .deps import get_database, get_model_runtime
from .errors import ApiError

logger = logging.getLogger("controlgauge_service.routes.model_endpoints")

router = APIRouter()

DatabaseDep = Annotated[Database, Depends(get_database)]
ModelRuntimeDep = Annotated[ModelRuntimeManager, Depends(get_model_runtime)]

#: Longest accepted base URL. A loopback ``scheme://host:port`` form never
#: approaches this; the bound exists so hostile input dies at the door.
BASE_URL_MAX_CHARS = 255
#: The loopback allowlist -- the ONLY hosts an endpoint may target.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
DISPLAY_NAME_MAX = 255

_ALL_ROLES: tuple[Role, Role] = ("generation", "embedding")

#: Registry codes -> HTTP status (alongside `_IMPORT_ERROR_STATUS` in
#: `routes/models.py`): request problems 422, conflicts 409, unknown id
#: 404, a probe that could not verify the endpoint 502.
_REGISTRY_ERROR_STATUS: dict[str, int] = {
    "registry_endpoint_invalid": 422,
    "registry_endpoint_not_loopback": 422,
    "registry_endpoint_unreachable": 502,
    "registry_duplicate": 409,
    "endpoint_not_found": 404,
    "endpoint_attached": 409,
}


def _registry_error(exc: ModelRuntimeError) -> ApiError:
    return ApiError(_REGISTRY_ERROR_STATUS[exc.code], exc.code, str(exc))


def _validation_field_error(field: str) -> ApiError:
    return ApiError(
        422,
        "validation_failed",
        "The request is not valid.",
        fields=[{"field": field, "code": "invalid", "message": "This value is not valid."}],
    )


# --- request/response schemas (closed, strict) --------------------------------


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: StrictStr
    backend: Literal["llama_cpp", "openai_compatible"]
    base_url: StrictStr
    capabilities: Literal["generation", "embedding", "both"]


class EndpointOut(BaseModel):
    id: str
    display_name: str
    backend: str
    #: The normalized ``scheme://host:port`` form. Loopback by
    #: construction and free of userinfo/path/query -- there is no
    #: sensitive remainder to echo (T-10).
    base_url_host_only: str
    capabilities: str
    registered_at: datetime
    last_health_at: datetime | None
    last_health_state: str | None
    last_health_code: str | None


class EndpointList(BaseModel):
    endpoints: list[EndpointOut]


class EndpointAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["generation", "embedding"]


class AttachResult(BaseModel):
    """The 200 body of a successful attach: the role now occupied by the
    endpoint (its registry id is the role's ``model_id``)."""

    role: str
    state: str
    model_id: str


# --- helpers --------------------------------------------------------------------


def _clean_endpoint_name(raw: str) -> str:
    """Cleaned display text for an endpoint (the `routes/models.py`
    pattern: shared character class from `evidence/filetype.py`, local
    bound and fixed error code). Display text only -- never a path."""
    name = unicodedata.normalize("NFC", raw).strip()
    name = re.split(r"[\\/]", name)[-1].strip()
    if not name or name in {".", ".."}:
        raise ModelRuntimeError("registry_endpoint_invalid")
    if len(name) > DISPLAY_NAME_MAX or DISALLOWED_NAME_CHARS.search(name):
        raise ModelRuntimeError("registry_endpoint_invalid")
    return name


def _validate_loopback_base_url(raw: str) -> str:
    """Validate a base URL and return its normalized ``scheme://host:port``
    form. Fail-closed order: loopback host FIRST (a non-loopback host is
    a security refusal, `registry_endpoint_not_loopback`), then scheme
    http + no userinfo/path/query/fragment + bounded length (anything
    else is `registry_endpoint_invalid`). Nothing about the value is
    echoed back -- the fixed code is all a client learns."""
    if not raw or len(raw) > BASE_URL_MAX_CHARS:
        raise ModelRuntimeError("registry_endpoint_invalid")
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if not host:
        # No parseable host at all: malformed, not a loopback question.
        raise ModelRuntimeError("registry_endpoint_invalid")
    # The security gate next: only loopback hosts may be registered. A
    # non-loopback host is refused as such regardless of scheme (an
    # https:// non-loopback URL is a security refusal, not a form error).
    if host not in LOOPBACK_HOSTS:
        raise ModelRuntimeError("registry_endpoint_not_loopback")
    if parts.scheme != "http":
        raise ModelRuntimeError("registry_endpoint_invalid")
    if parts.username is not None or parts.password is not None:
        # Credentials in a URL would be stored and echoed -- refused.
        raise ModelRuntimeError("registry_endpoint_invalid")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ModelRuntimeError("registry_endpoint_invalid")
    port = parts.port
    if ":" in host:  # IPv6 literal -- re-bracket for a usable URL
        normalized = f"http://[{host}]"
    else:
        normalized = f"http://{host}"
    if port is not None:
        normalized += f":{port}"
    return normalized


def _roles_for(capabilities: str) -> tuple[Role, ...]:
    """The capabilities an endpoint declares, as provider roles. ``both``
    means BOTH must pass every probe (a half-working endpoint is not
    ready)."""
    if capabilities == "generation":
        return ("generation",)
    if capabilities == "embedding":
        return ("embedding",)
    return ("generation", "embedding")


def _probe(display_name: str, backend: str, base_url: str, role: Role) -> str | None:
    """One bounded real health check for one declared capability.
    Returns the failure's ``MODEL_RUNTIME_ERROR_CODES`` detail code, or
    ``None`` when ready. The probe provider is throwaway (its ``stop()``
    closes only our client -- the endpoint keeps running)."""
    provider = ExternalEndpointProvider("probe", display_name, base_url, role, backend=backend)
    try:
        status = provider.health_check()
    finally:
        provider.stop()
    if status.state == "ready":
        return None
    return status.detail_code or "invalid_output"


def _to_out(row: ExternalEndpoint) -> EndpointOut:
    return EndpointOut(
        id=row.id,
        display_name=row.display_name,
        backend=row.backend,
        base_url_host_only=row.base_url,
        capabilities=row.capabilities,
        registered_at=row.registered_at,
        last_health_at=row.last_health_at,
        last_health_state=row.last_health_state,
        last_health_code=row.last_health_code,
    )


def _get_endpoint(database: Database, endpoint_id: str) -> ExternalEndpoint:
    with database.session() as session:
        row = session.get(ExternalEndpoint, endpoint_id)
    if row is None:
        raise ApiError(404, "endpoint_not_found", str(ModelRuntimeError("endpoint_not_found")))
    return row


def _check_duplicate(database: Database, backend: str, base_url: str, capabilities: str) -> None:
    with database.session() as session:
        existing = session.scalar(
            select(ExternalEndpoint).where(
                ExternalEndpoint.backend == backend,
                ExternalEndpoint.base_url == base_url,
                ExternalEndpoint.capabilities == capabilities,
            )
        )
    if existing is not None:
        raise ModelRuntimeError("registry_duplicate")


def _unreachable(failure_code: str) -> ApiError:
    """The 502 registration refusal. The failing capability's fixed
    detail code rides as a field message -- a structural
    ``MODEL_RUNTIME_ERROR_CODES`` value, allowed by T-10."""
    return ApiError(
        502,
        "registry_endpoint_unreachable",
        str(ModelRuntimeError("registry_endpoint_unreachable")),
        fields=[
            {"field": "base_url", "code": "unreachable", "message": failure_code},
        ],
    )


# --- routes ---------------------------------------------------------------------


@router.post("/model-endpoints")
def register_endpoint(payload: EndpointCreate, database: DatabaseDep) -> JSONResponse:
    """Register a loopback endpoint: validate the URL, probe EVERY
    declared capability with a bounded real health check, persist only
    on full success (a probe failure is a 502 and NO row -- an endpoint
    we cannot verify is not registered). A successful registration
    records ``last_health_state="ready"`` from that probe."""
    try:
        display_name = _clean_endpoint_name(payload.display_name)
    except ModelRuntimeError as exc:
        raise _registry_error(exc) from None
    try:
        base_url = _validate_loopback_base_url(payload.base_url)
    except ModelRuntimeError as exc:
        raise _registry_error(exc) from None
    try:
        _check_duplicate(database, payload.backend, base_url, payload.capabilities)
    except ModelRuntimeError as exc:
        raise _registry_error(exc) from None

    endpoint_id = str(uuid.uuid4())
    for role in _roles_for(payload.capabilities):
        failure = _probe(display_name, payload.backend, base_url, role)
        if failure is not None:
            logger.info("endpoint_registration_refused", extra={"error_code": failure})
            raise _unreachable(failure) from None

    now = datetime.now(UTC)
    row = ExternalEndpoint(
        id=endpoint_id,
        display_name=display_name,
        backend=payload.backend,
        base_url=base_url,
        capabilities=payload.capabilities,
        registered_at=now,
        last_health_at=now,
        last_health_state="ready",
        last_health_code=None,
    )
    with database.session(write=True) as session:
        session.add(row)
        session.flush()
    logger.info(
        "endpoint_registered",
        extra={"endpoint_id": row.id, "backend": row.backend, "capabilities": row.capabilities},
    )
    return JSONResponse(_to_out(row).model_dump(mode="json"), status_code=201)


@router.get("/model-endpoints")
def list_endpoints(database: DatabaseDep) -> EndpointList:
    """Every registered endpoint with its RECORDED last health result:
    structural only, never a live probe (the live check is the explicit
    POST /health)."""
    with database.session() as session:
        rows: Any = session.scalars(
            select(ExternalEndpoint).order_by(
                ExternalEndpoint.registered_at.desc(), ExternalEndpoint.id.desc()
            )
        ).all()
    return EndpointList(endpoints=[_to_out(row) for row in rows])


@router.delete("/model-endpoints/{endpoint_id}")
def delete_endpoint(
    endpoint_id: str, database: DatabaseDep, manager: ModelRuntimeDep
) -> JSONResponse:
    """Unregister an endpoint. Refused while it is the occupant of a
    running role (409 `endpoint_attached`) -- detach via the role's stop
    route first."""
    row = _get_endpoint(database, endpoint_id)
    for role in _ALL_ROLES:
        if manager.loaded_model_id(role) == endpoint_id:
            raise ApiError(409, "endpoint_attached", str(ModelRuntimeError("endpoint_attached")))
    with database.session(write=True) as session:
        attached = session.get(ExternalEndpoint, row.id)
        if attached is not None:
            session.delete(attached)
            session.flush()
    logger.info("endpoint_deleted", extra={"endpoint_id": row.id})
    return JSONResponse({"deleted": True}, status_code=200)


@router.post("/model-endpoints/{endpoint_id}/health")
def check_endpoint_health(endpoint_id: str, database: DatabaseDep) -> EndpointOut:
    """A live bounded check for EVERY declared capability; the worst
    result is recorded on the row (`ready` only when all pass --
    `endpoint_health_state` maps a failure's detail code onto
    ``unreachable`` vs ``unhealthy``)."""
    row = _get_endpoint(database, endpoint_id)
    failure: str | None = None
    for role in _roles_for(row.capabilities):
        failure = _probe(row.display_name, row.backend, row.base_url, role)
        if failure is not None:
            break  # first failing capability decides; deterministic order
    state = endpoint_health_state(failure)
    with database.session(write=True) as session:
        fresh = session.get(ExternalEndpoint, row.id)
        if fresh is not None:
            fresh.last_health_at = datetime.now(UTC)
            fresh.last_health_state = state
            fresh.last_health_code = failure
            row = fresh
            session.flush()
    return _to_out(row)


@router.post("/model-endpoints/{endpoint_id}/attach")
def attach_endpoint(
    endpoint_id: str,
    payload: EndpointAttachRequest,
    database: DatabaseDep,
    manager: ModelRuntimeDep,
) -> AttachResult:
    """Make the endpoint the role's occupant: constructs the provider and
    hands it to the manager (`attach` -- the switch contract applies;
    ``start()`` is never called because the endpoint is already running).
    Subsequent generate/embed traffic for the role flows through it."""
    row = _get_endpoint(database, endpoint_id)
    role: Role = payload.role
    if payload.role not in _roles_for(row.capabilities):
        # An embedding-only endpoint cannot serve generation (and vice
        # versa); "both" serves either. Fail closed, not silently.
        raise _validation_field_error("role")
    provider = ExternalEndpointProvider(
        row.id, row.display_name, row.base_url, role, backend=row.backend
    )
    try:
        manager.attach(role, provider, display_name=row.display_name)
    except ModelRuntimeError as exc:
        provider.stop()
        status = _REGISTRY_ERROR_STATUS.get(exc.code, 502)
        raise ApiError(status, exc.code, str(exc)) from None
    logger.info("endpoint_attached", extra={"endpoint_id": row.id, "role": role})
    return AttachResult(role=role, state="ready", model_id=row.id)
