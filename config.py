import os
import json
import logging

logger = logging.getLogger('autotrader.config')

DEFAULT_CONNECTION_CONFIG = {
    'host': '127.0.0.1',
    'port': 11111,
    'readonly': True,
    'portfolio_env': 'SIMULATE',
    'security_firm': 'FUTUSECURITIES',
    'account_id': '',
    'db_path': 'options.db',
    'auto_launch_opend': False,
    'opend_path': ''
}


def apply_env_overrides(config):
    env_mapping = {
        'host': 'MOOMOO_OPEND_HOST',
        'port': 'MOOMOO_OPEND_PORT',
        'portfolio_env': 'MOOMOO_PORTFOLIO_ENV',
        'security_firm': 'MOOMOO_SECURITY_FIRM',
        'account_id': 'MOOMOO_ACCOUNT_ID'
    }

    for key, env_var in env_mapping.items():
        env_value = os.environ.get(env_var)
        if env_value is None or env_value == '':
            continue

        if key == 'port':
            try:
                config[key] = int(env_value)
            except ValueError:
                logger.warning(f"Ignoring invalid integer for {env_var}: {env_value}")
            continue

        config[key] = env_value

    readonly_override = os.environ.get('MOOMOO_READONLY')
    if readonly_override is not None and readonly_override != '':
        config['readonly'] = readonly_override.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

    return config

class Config:
    """
    Configuration class for the AutoTrader application
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
            env_config_file = os.environ.get('CONNECTION_CONFIG', 'connection.json')
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
            with open(config_file, 'r') as f:
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
            with open(config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving configuration to {config_file}: {str(e)}")
            return False 
