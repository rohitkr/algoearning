"""One error shape for every failure:

    {"error": {"code": "not_found", "message": "...", "details": ..., "request_id": "..."}}

Raise an AppError subclass anywhere; validation errors and unexpected exceptions are converted too.
Unexpected errors never leak internals to the client (the log has the traceback, keyed by request id)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("ae_api.errors")


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.message, self.details = message, details


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Forbidden(AppError):
    status, code = 403, "forbidden"


CLOSED_ALPHA = "Internal Alpha Test Environment. Closed to the public."


class RegistrationClosed(Forbidden):
    """A sign-in we have no account for, while new accounts are refused (Settings.accepts_new_users)."""

    def __init__(self) -> None:
        super().__init__(CLOSED_ALPHA, {"reason": "registration_closed"})


class PlanLimitReached(AppError):
    """The user's plan does not allow more of something. details: feature, limit, used, plan."""

    status, code = 403, "plan_limit"


class NotInPlan(AppError):
    """The user's plan does not include a feature. details: feature, plan."""

    status, code = 403, "plan_feature"


class NotFound(AppError):
    status, code = 404, "not_found"


class Invalid(AppError):
    """Well-formed but breaks a rule. details: [{loc, msg, type}], like request validation errors."""

    status, code = 422, "validation_error"


class Conflict(AppError):
    status, code = 409, "conflict"


class TooManyRequests(AppError):
    """An upstream service (e.g. Telegram's flood wait) asks to wait; details carry retry_after seconds."""

    status, code = 429, "rate_limited"


class Unavailable(AppError):
    status, code = 503, "unavailable"


def _body(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    rid = structlog.contextvars.get_contextvars().get("request_id")
    return {"error": {"code": code, "message": message, "details": details, "request_id": rid}}


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(_body(request, "validation_error", "request is invalid", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(_body(request, code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", path=request.url.path)
        return JSONResponse(_body(request, "internal_error", "something went wrong"), status_code=500)
