"""
Shared Config Service

Provides a singleton Config instance to avoid repeated config file parsing.
All services should use get_config() instead of creating new Config() instances.
"""

from config import Config as BaseConfig

# Singleton instance
_config_instance = None


def get_config():
    """
    Get or create the singleton Config instance.

    This ensures the config file is only read once at startup,
    avoiding repeated file I/O and environment variable parsing.

    Returns:
        Config: The singleton Config instance

    Example:
        from api.services.config import get_config

        config = get_config()
        db_path = config.get('db_path')
        host = config.get('host')
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = BaseConfig()
    return _config_instance


def get_current_identity():
    """Return the (env, opaque_account_id) identity for the current runtime.

    Used to scope history/journal reads to this environment and account (C04).
    The opaque id is derived exactly as wheel_runner does so reads and writes
    agree on the same book.
    """
    from core.wheel_runner import opaque_account_id

    cfg = get_config()
    env = str(cfg.get("portfolio_env", "SIMULATE")).strip().upper()
    account = str(cfg.get("account_id", "") or "").strip()
    return env, opaque_account_id(account)
