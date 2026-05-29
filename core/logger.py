"""Logger with per-request req_id propagation.

Usage anywhere:
    from core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("hello")
    logger.error("boom")          # exc_info auto-attached when inside an except block
    logger.exception("critical")  # always attaches exc_info (stdlib behaviour)

The req_id is stored in a ContextVar and is automatically injected into every
log record. It is set per-request by `RequestIdMiddleware` and can also be
set manually via `set_req_id()` in workers / background jobs.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

from core.config import settings

# ---------------------------------------------------------------------------
# Request id context (async-safe; one value per request / task)
# ---------------------------------------------------------------------------
_req_id_ctx: ContextVar[str] = ContextVar("req_id", default="-")


def set_req_id(req_id: str) -> None:
    _req_id_ctx.set(req_id)


def get_req_id() -> str:
    return _req_id_ctx.get()


# ---------------------------------------------------------------------------
# Filter — injects req_id into every log record
# ---------------------------------------------------------------------------
class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.req_id = _req_id_ctx.get()
        return True


# ---------------------------------------------------------------------------
# Format
# %(name)s  — dotted logger name, e.g. "api.v1.demo"
# ---------------------------------------------------------------------------
_FORMAT = "%(asctime)s %(levelname)-8s [%(req_id)s] %(name)s:%(lineno)d  %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"

_LEVEL_MAP = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


# ---------------------------------------------------------------------------
# Adapter — auto-injects req_id; attaches exc_info on error/critical only
# when called from inside an active exception handler (via sys.exc_info)
# ---------------------------------------------------------------------------
class RequestLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})["req_id"] = _req_id_ctx.get()
        return msg, kwargs

    def error(self, msg, *args, **kwargs):
        kwargs.setdefault("exc_info", sys.exc_info()[0] is not None)
        super().error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        kwargs.setdefault("exc_info", sys.exc_info()[0] is not None)
        super().critical(msg, *args, **kwargs)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _configure_root() -> None:
    """Configure the root logger once. Idempotent."""
    root = logging.getLogger()
    if getattr(root, "_configured", False):
        return

    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FMT))
    handler.addFilter(_ContextFilter())

    level = _LEVEL_MAP.get(settings.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    root._configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> RequestLoggerAdapter:
    """Return a logger adapter that auto-injects the current req_id."""
    _configure_root()
    return RequestLoggerAdapter(logging.getLogger(name), {})


# Default module-level logger — `from core.logger import logger`
logger = get_logger("app")

