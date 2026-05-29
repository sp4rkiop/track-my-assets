"""HTTP middlewares."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.logger import get_logger, set_req_id

REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a unique req_id per request and exposes it on the response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        set_req_id(req_id)

        logger.info(
            "request.start",
            extra={"method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request.error")
            raise

        response.headers[REQUEST_ID_HEADER] = req_id
        logger.info(
            "request.end",
            extra={"status_code": response.status_code, "path": request.url.path},
        )
        return response

