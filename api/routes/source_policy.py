"""
Helpers for attaching data-source policy metadata to API payloads.
"""

from __future__ import annotations


_EXTERNAL_SOURCE_ALIASES = {
    "yfinance": "yfinance",
    "yahoo": "yfinance",
    "openbb": "openbb",
    "alpha vantage": "alpha_vantage",
    "alpha_vantage": "alpha_vantage",
    "alphavantage": "alpha_vantage",
}

_BROKER_SOURCE_ALIASES = {
    "broker",
    "moomoo",
    "opend",
    "portfolio_fallback",
    "portfolio fallback",
    "cached_broker",
    "cached broker",
}


def attach_source_policy(payload, source_policy):
    if not isinstance(payload, dict):
        return {"data": payload, "source_policy": source_policy}
    enriched = dict(payload)
    enriched["source_policy"] = source_policy
    return enriched


def build_account_source_policy(domain):
    return {
        "domain": domain,
        "mode": "broker_only",
        "source_of_truth": "opend",
        "external_fallback_allowed": False,
        "description": "Portfolio, positions, balances, cash, and buying power come only from OpenD/moomoo.",
    }


def build_research_source_policy(domain, payload=None, fallback_sources_allowed=None):
    allowed = list(fallback_sources_allowed or [])
    used = sorted(detect_external_sources(payload)) if payload is not None else []
    return {
        "domain": domain,
        "mode": "research_with_fallbacks",
        "source_of_truth": "opend_for_account_state",
        "external_fallback_allowed": bool(allowed),
        "external_fallback_sources_allowed": allowed,
        "external_fallback_sources_used": used,
        "description": "Fallback market data may assist research, but account state remains broker-authoritative.",
    }


def detect_external_sources(payload):
    sources = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                lowered_key = str(key).lower()
                if lowered_key == "from_yfinance" and bool(item):
                    sources.add("yfinance")
                elif lowered_key.endswith("_source") or lowered_key == "data_source":
                    normalized = _normalize_source(item)
                    if normalized is not None:
                        sources.add(normalized)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return sources


def _normalize_source(value):
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if not lowered or lowered in _BROKER_SOURCE_ALIASES:
        return None
    if lowered in _EXTERNAL_SOURCE_ALIASES:
        return _EXTERNAL_SOURCE_ALIASES[lowered]
    return None
