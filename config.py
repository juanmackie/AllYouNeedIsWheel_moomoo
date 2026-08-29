import json
import logging
import os

logger = logging.getLogger("ayniwheel.config")


def _parse_watchlist_env(raw_watchlist):
    if not raw_watchlist:
        return []
    return [ticker.strip().upper() for ticker in raw_watchlist.split(",") if ticker.strip()]


DEFAULT_CONNECTION_CONFIG = {
    "host": "127.0.0.1",
    "port": 11111,
    "portfolio_env": "SIMULATE",
    "security_firm": "FUTUSECURITIES",
    "account_id": "",
    "db_path": "options.db",
    "auto_launch_opend": False,
    "opend_path": "",
    "cash_reserve_enabled": True,
    "watchlist_mode": "static",  # 'static' or 'moomoo' (dynamic/hybrid screening removed 2026-08)
    "moomoo_watchlist_group": "My Watchlist",
    "broker_cache_after_hours": True,  # use cached broker data outside US market hours
    # Conservative option-chain quota defaults; tune only after observing OpenD.
    "chain_rate_limit_max_requests": 10,
    "chain_rate_limit_window_sec": 30,
    "chain_min_request_spacing_sec": 3.0,
    "auto_refresh_at_open": False,
    "watchlist": _parse_watchlist_env(os.environ.get("WATCHLIST")),
    # Versioned wheel risk preset (conservative | balanced | aggressive).
    # The UI persists the selection in the settings table; this is only the default.
    "wheel_preset": "balanced",
}


def apply_env_overrides(config):
    env_mapping = {
        "host": "MOOMOO_OPEND_HOST",
        "port": "MOOMOO_OPEND_PORT",
        "portfolio_env": "MOOMOO_PORTFOLIO_ENV",
        "security_firm": "MOOMOO_SECURITY_FIRM",
        "account_id": "MOOMOO_ACCOUNT_ID",
        "chain_rate_limit_max_requests": "MOOMOO_CHAIN_RATE_LIMIT_MAX_REQUESTS",
        "chain_rate_limit_window_sec": "MOOMOO_CHAIN_RATE_LIMIT_WINDOW_SEC",
        "chain_min_request_spacing_sec": "MOOMOO_CHAIN_MIN_REQUEST_SPACING_SEC",
        "auto_refresh_at_open": "MOOMOO_AUTO_REFRESH_AT_OPEN",
    }

    for key, env_var in env_mapping.items():
        env_value = os.environ.get(env_var)
        if env_value is None or env_value == "":
            continue

        if key in {"port", "chain_rate_limit_max_requests", "chain_rate_limit_window_sec"}:
            try:
                config[key] = int(env_value)
            except ValueError:
                logger.warning(f"Ignoring invalid integer for {env_var}: {env_value}")
            continue
        if key == "chain_min_request_spacing_sec":
            try:
                config[key] = float(env_value)
            except ValueError:
                logger.warning(f"Ignoring invalid number for {env_var}: {env_value}")
            continue
        if key == "auto_refresh_at_open":
            config[key] = env_value.strip().lower() in {"1", "true", "yes", "y", "on"}
            continue

        config[key] = env_value

    readonly_override = os.environ.get("MOOMOO_READONLY")
    if readonly_override is not None and readonly_override != "":
        # The app is structurally query-only; readonly is always True.
        # Accept and ignore the variable rather than breaking existing configs.
        logger.info("MOOMOO_READONLY accepted for compatibility; the app is query-only.")

    cash_reserve_enabled = os.environ.get("CASH_RESERVE_ENABLED")
    if cash_reserve_enabled is not None and cash_reserve_enabled != "":
        config["cash_reserve_enabled"] = cash_reserve_enabled.strip().lower() in {"1", "true", "yes", "y", "on"}

    watchlist_env = os.environ.get("WATCHLIST")
    if watchlist_env is not None and watchlist_env != "":
        config["watchlist"] = _parse_watchlist_env(watchlist_env)

    return config


class Config:
    """
    Configuration for the All You Need Is Wheel application
    """

    def __init__(self, default_config=None, config_file=None):
        """
        Initialize the configuration with default values and load from a file if provided

        Args:
            default_config (dict, optional): Default configuration values. Defaults to None.
            config_file (str, optional): Path to a JSON configuration file. Defaults to None.
        """
        # Initialize with default values
        self.config = DEFAULT_CONNECTION_CONFIG.copy()
        if default_config:
            self.config.update(default_config)
        apply_env_overrides(self.config)

        # If config_file is not provided, check environment variable
        if config_file is None:
            env_config_file = os.environ.get("CONNECTION_CONFIG", "connection.json")
            if os.path.exists(env_config_file):
                config_file = env_config_file
                logger.info(f"Using connection config from environment: {env_config_file}")

        # Load from file if provided
        if config_file and os.path.exists(config_file):
            self.load_from_file(config_file)
            logger.info(f"Configuration loaded from: {config_file}")
            logger.debug(f"Connection port: {self.get('port')}")

    def load_from_file(self, config_file):
        """
        Load configuration from a JSON file

        Args:
            config_file (str): Path to a JSON configuration file

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with open(config_file, "r") as f:
                file_config = json.load(f)

            # Update our configuration with values from the file
            self.config.update(file_config)
            apply_env_overrides(self.config)
            return True
        except Exception as e:
            logger.error(f"Error loading configuration from {config_file}: {str(e)}")
            return False

    def get(self, key, default=None):
        """
        Get a configuration value

        Args:
            key (str): Configuration key
            default: Default value to return if the key is not found

        Returns:
            The configuration value or default
        """
        return self.config.get(key, default)

    def set(self, key, value):
        """
        Set a configuration value

        Args:
            key (str): Configuration key
            value: Value to set
        """
        self.config[key] = value

    def to_dict(self):
        """
        Get the entire configuration as a dictionary

        Returns:
            dict: Configuration dictionary
        """
        return self.config.copy()

    def save_to_file(self, config_file):
        """
        Save the configuration to a JSON file

        Args:
            config_file (str): Path to a JSON configuration file

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with open(config_file, "w") as f:
                json.dump(self.config, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving configuration to {config_file}: {str(e)}")
            return False
