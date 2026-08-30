"""
Context creation helpers for moomoo OpenD connections.
Extracted from core/connection.py for maintainability.
"""

import socket

from moomoo import (
    OpenQuoteContext,
    OpenSecTradeContext,
    TrdMarket,
)

from core.logging_config import get_logger

logger = get_logger("ayniwheel.connection", "moomoo")


class QueryOnlyTradeContext:
    """Expose only the trade-context queries used by this application."""

    def __init__(self, context):
        self._context = context

    def get_acc_list(self, *args, **kwargs):
        return self._context.get_acc_list(*args, **kwargs)

    def accinfo_query(self, *args, **kwargs):
        return self._context.accinfo_query(*args, **kwargs)

    def position_list_query(self, *args, **kwargs):
        return self._context.position_list_query(*args, **kwargs)

    def close(self):
        return self._context.close()


def probe_opend_status(host="127.0.0.1", port=11111):
    """
    Probe the local OpenD endpoint and return a UI-friendly status payload.
    Uses only a lightweight TCP socket check - no OpenQuoteContext creation
    to avoid connection cycling issues.
    """
    status = {
        "status": "unknown",
        "connected": False,
        "reachable": False,
        "host": host,
        "port": port,
        "message": "Checking OpenD status...",
        "details": {},
    }

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        status["reachable"] = sock.connect_ex((host, int(port))) == 0
    except Exception as exc:
        status["status"] = "error"
        status["message"] = f"Could not probe OpenD port: {exc}"
        return status
    finally:
        sock.close()

    if not status["reachable"]:
        status["status"] = "unavailable"
        status["message"] = "OpenD is not running on the configured host and port."
        return status

    status["status"] = "connected"
    status["connected"] = True
    status["message"] = "OpenD is reachable (TCP probe passed)."
    return status


def create_contexts(host, port, security_firm):
    """
    Create OpenQuoteContext and OpenSecTradeContext instances.

    Args:
        host: OpenD host address
        port: OpenD port
        security_firm: SecurityFirm enum value

    Returns:
        tuple: (quote_ctx, trd_ctx)
    """
    quote_ctx = OpenQuoteContext(host=host, port=port)
    raw_trd_ctx = OpenSecTradeContext(
        host=host,
        port=port,
        filter_trdmarket=TrdMarket.NONE,
        security_firm=security_firm,
    )
    return quote_ctx, QueryOnlyTradeContext(raw_trd_ctx)
