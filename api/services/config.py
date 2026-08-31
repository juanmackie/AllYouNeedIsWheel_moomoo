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
