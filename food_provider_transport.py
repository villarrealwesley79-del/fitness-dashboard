"""Small safe GET/JSON transport shared by USDA, UPC, and OFF clients."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib import error, request


logger = logging.getLogger(__name__)
_remaining_timeout: ContextVar[float | None] = ContextVar("food_provider_remaining_timeout", default=None)


@contextmanager
def remaining_timeout(seconds: float):
    """Apply a request-local timeout ceiling without process-global state."""
    token = _remaining_timeout.set(max(0.0, float(seconds)))
    try:
        yield
    finally:
        _remaining_timeout.reset(token)


def clamp_timeout(timeout: float) -> float:
    remaining = _remaining_timeout.get()
    return float(timeout) if remaining is None else min(float(timeout), remaining)


def get_json(req: request.Request, *, timeout: float, provider: str) -> Any | None:
    """Fetch one JSON response and emit one safe provider warning on failure."""
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            if status is not None and not 200 <= int(status) < 300:
                logger.warning("%s provider warning: non-2xx response", provider)
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError:
        logger.warning("%s provider warning: non-2xx response", provider)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("%s provider warning: malformed JSON", provider)
        return None
    except TimeoutError:
        logger.warning("%s provider warning: timeout", provider)
        return None
    except (OSError, error.URLError):
        logger.warning("%s provider warning: network failure", provider)
        return None
    if not isinstance(payload, dict):
        logger.warning("%s provider warning: malformed provider response", provider)
        return None
    return payload
