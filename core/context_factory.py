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
    trd_ctx = OpenSecTradeContext(
        host=host,
        port=port,
        filter_trdmarket=TrdMarket.NONE,
        security_firm=security_firm,
    )
    return quote_ctx, trd_ctx
