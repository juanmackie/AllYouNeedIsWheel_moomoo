"""
Backward-compatible re-exports from decomposed modules.
core/connection.py has been split into:
  - connection_constants.py (pure utility functions)
  - context_factory.py (context creation, probe_opend_status)
  - ticker_utils.py (ticker formatting and caching)
  - connection_manager.py (MoomooConnection lifecycle and operations)
"""

from core.connection_constants import (
    _safe_close_context,
    _is_truthy_flag,
    _clean_account_id,
    _env_name,
    _normalize_trd_env,
    _normalize_security_firm,
    _infer_security_type_from_code,
    _parse_option_code_metadata,
    _safe_float,
    _first_non_zero,
)

from core.context_factory import probe_opend_status, create_contexts

from core.ticker_utils import TickerCache, format_symbol

# MoomooConnection is lazy-loaded via __getattr__ to avoid
# triggering moomoo SDK imports at module-load time.

__all__ = [
    'MoomooConnection',
    'TrdEnv',
    'SecurityFirm',
    'RET_OK',
    'RET_ERROR',
    'probe_opend_status',
    'create_contexts',
    'TickerCache',
    'format_symbol',
    '_safe_close_context',
    '_is_truthy_flag',
    '_clean_account_id',
    '_env_name',
    '_normalize_trd_env',
    '_normalize_security_firm',
    '_infer_security_type_from_code',
    '_parse_option_code_metadata',
    '_safe_float',
    '_first_non_zero',
]


def __getattr__(name):
    if name == 'MoomooConnection':
        from core.connection_manager import MoomooConnection as _mc
        globals()['MoomooConnection'] = _mc
        return _mc
    if name in {'TrdEnv', 'SecurityFirm', 'RET_OK', 'RET_ERROR'}:
        import importlib
        moomoo = importlib.import_module('moomoo')
        value = {
            'TrdEnv': moomoo.TrdEnv,
            'SecurityFirm': moomoo.SecurityFirm,
            'RET_OK': moomoo.RET_OK,
            'RET_ERROR': moomoo.RET_ERROR,
        }[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
