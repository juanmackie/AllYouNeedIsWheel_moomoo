"""
Moomoo connection constants and pure utility functions.
Extracted from core/connection.py for maintainability.
"""

import re
from moomoo import (
    SecurityFirm,
    TrdEnv,
)

from core.logging_config import get_logger

logger = get_logger('autotrader.connection', 'moomoo')


def _safe_close_context(context):
    if context is None:
        return
    try:
        context.close()
    except Exception:
        pass


def _is_truthy_flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'ok', 'connected', 'ready'}
    return False


def _clean_account_id(value):
    if value is None:
        return ''

    account_id = str(value).strip()
    if not account_id or account_id.upper() == 'YOUR_MOOMOO_ACCOUNT_ID':
        return ''

    return account_id


def _env_name(trd_env):
    return 'SIMULATE' if trd_env == TrdEnv.SIMULATE else 'REAL'


def _normalize_trd_env(value, default_env):
    if value is None:
        return default_env

    if value in (TrdEnv.SIMULATE, TrdEnv.REAL):
        return value

    text = str(value).strip().upper()
    if text in {'SIM', 'SIMULATE', 'PAPER'}:
        return TrdEnv.SIMULATE
    if text in {'REAL', 'LIVE'}:
        return TrdEnv.REAL

    return default_env


def _normalize_security_firm(value, default_firm=SecurityFirm.FUTUSECURITIES):
    if value is None:
        return default_firm

    if value in {
        SecurityFirm.FUTUSECURITIES,
        SecurityFirm.FUTUINC,
        SecurityFirm.FUTUSG,
        SecurityFirm.FUTUAU,
        SecurityFirm.FUTUCA,
        SecurityFirm.FUTUJP,
        SecurityFirm.FUTUMY,
    }:
        return value

    attr_name = str(value).strip().upper()
    if hasattr(SecurityFirm, attr_name):
        return getattr(SecurityFirm, attr_name)

    logger.warning(f"Unknown moomoo security firm '{value}', falling back to {default_firm}")
    return default_firm


def _infer_security_type_from_code(code):
    if not code:
        return ''

    normalized = str(code).strip()
    option_pattern = r'^[A-Z]{2}\.[A-Z]+\d{6}[CP]\d+$'
    if re.match(option_pattern, normalized):
        return 'OPT'

    stock_pattern = r'^[A-Z]{2}\.[A-Z]+$'
    if re.match(stock_pattern, normalized):
        return 'STK'

    return ''


def _parse_option_code_metadata(code):
    if not code:
        return None

    normalized = str(code).strip()
    suffix = normalized.split('.', 1)[1] if '.' in normalized else normalized
    match = re.match(r'^(?P<underlying>[A-Z]+)(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d+)$', suffix)
    if not match:
        return None

    expiry = match.group('expiry')
    year = 2000 + int(expiry[0:2])
    month = expiry[2:4]
    day = expiry[4:6]

    strike_digits = match.group('strike')
    strike_price = int(strike_digits) / 1000

    return {
        'underlying': match.group('underlying'),
        'expiration': f"{year:04d}{month}{day}",
        'strike': strike_price,
        'option_type': 'CALL' if match.group('right') == 'C' else 'PUT'
    }


def _safe_float(value, default=0.0):
    if value in (None, '', 'N/A', 'nan', 'NaN'):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_iv(value, default=0.0):
    """
    Normalize implied volatility to decimal form.

    Moomoo returns IV as percentage (e.g. 48.0 for 48%), while Black-Scholes
    and the rest of the system expect decimal (e.g. 0.48 for 48%).
    Values > 3.0 are assumed to be percentages and divided by 100.
    Values already in decimal form (<= 3.0) are passed through unchanged.
    """
    iv = _safe_float(value, default)
    if iv > 3.0:
        return round(iv / 100.0, 4)
    return round(iv, 4)


def _first_non_zero(*values):
    for value in values:
        numeric_value = _safe_float(value, None)
        if numeric_value is not None and numeric_value != 0:
            return numeric_value

    for value in values:
        numeric_value = _safe_float(value, None)
        if numeric_value is not None:
            return numeric_value

    return 0.0
