"""
Auto-Trader API
Flask application initialization and configuration.
"""

from flask import Flask
from flask_cors import CORS
from core.logging_config import get_logger

# Configure logging
logger = get_logger('autotrader.api', 'api')

def create_app(config=None):
    """
    Create and configure the Flask application.
    
    Args:
        config (dict, optional): Configuration dictionary
        
    Returns:
        Flask: Configured Flask application
    """
    logger.info("Creating API application")
    app = Flask(__name__, 
                static_folder='../frontend/static',
                template_folder='../frontend/templates')
    
    # Enable CORS
    CORS(app)
    logger.debug("CORS enabled for API")
    
    # Default configuration
    app.config.from_mapping(
        SECRET_KEY='dev',
        DATABASE='sqlite:///:memory:',
    )
    
    # Override with passed config
    if config:
        app.config.update(config)
        logger.debug("Applied custom configuration")
    
    # Register blueprints
    from api.routes import portfolio, options
    app.register_blueprint(portfolio.bp)
    app.register_blueprint(options.bp)
    logger.info("Registered API blueprints")
    
    @app.route('/health')
    def health_check():
        logger.debug("Health check endpoint called")
        return {'status': 'healthy'}
        
    logger.info("API application created successfully")
    return app 