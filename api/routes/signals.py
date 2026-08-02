"""
Signal overlay API routes.

Read-only endpoint for the multi-dimensional moomoo overlay.
"""

from flask import Blueprint, request

from api.routes.utils import enforce_route_rate_limit, error_response, normalize_ticker_list, success_response

bp = Blueprint("signals", __name__, url_prefix="/api/signals")


def _parse_tickers():
    raw = request.args.get("tickers", "") or request.args.get("ticker", "")
    if not raw:
        return [], []
    return normalize_ticker_list(raw)


def _load_on_demand_evidence(tickers):
    catalyst_warnings = {}
    underlying_quality = {}
    enrichment_errors = []

    try:
        from api.services.catalyst_flow_service import CatalystFlowService

        catalyst_svc = CatalystFlowService()
        for ticker in tickers:
            catalyst_warnings[ticker] = catalyst_svc.get_ticker_warnings(ticker)
    except Exception as exc:
        enrichment_errors.append(f"catalyst_warnings unavailable: {exc}")

    try:
        from api.services.underlying_quality import get_underlying_quality

        for ticker in tickers:
            try:
                underlying_quality[ticker] = get_underlying_quality(ticker)
            except Exception as exc:
                enrichment_errors.append(f"underlying_quality unavailable for {ticker}: {exc}")
    except Exception as exc:
        enrichment_errors.append(f"underlying_quality unavailable: {exc}")

    return catalyst_warnings, underlying_quality, enrichment_errors


@bp.route("/overlay", methods=["GET"])
def get_signal_overlay():
    try:
        allowed, retry_after = enforce_route_rate_limit(
            "signal-overlay",
            request.remote_addr or "local",
            max_requests=60,
            window_seconds=60,
        )
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

        valid_tickers, invalid_tickers = _parse_tickers()
        if not valid_tickers:
            return error_response("ticker or tickers query parameter is required", status_code=400)
        valid_tickers = [ticker.upper() for ticker in valid_tickers]

        refresh = request.args.get("refresh", "false").lower() == "true"
        try:
            import api

            overlay_svc = api.get_service("signal_overlay")
        except Exception as exc:
            return error_response(f"Overlay service unavailable: {exc}", status_code=500)

        result = overlay_svc.get_overlays(valid_tickers, refresh=refresh)
        catalyst_warnings, underlying_quality, enrichment_errors = _load_on_demand_evidence(valid_tickers)
        payload = {
            "generated_at": result.get("generated_at"),
            "count": result.get("count", 0),
            "source_available": result.get("source_available", False),
            "overlays": result.get("overlays", {}),
            "catalyst_warnings": catalyst_warnings,
            "underlying_quality": underlying_quality,
            "errors": result.get("errors", []),
            "enrichment_errors": enrichment_errors,
            "invalid_tickers": sorted(set(invalid_tickers + result.get("invalid_tickers", []))),
            "refresh": refresh,
            "elapsed_seconds": result.get("elapsed_seconds"),
        }
        return success_response(payload)
    except Exception as exc:
        return error_response(str(exc), status_code=500)
