"""
Auto-Trader Web Application
Main entry point for the web application
"""

import os
import json
from flask import Flask, render_template, request, redirect, url_for, jsonify
from api import create_app
from core.logging_config import get_logger
from db.database import OptionsDatabase
from core.connection import MoomooConnection
from config import apply_env_overrides

# Configure logging
logger = get_logger('autotrader.app', 'api')


def _resolve_local_path(path_value, base_dir):
    if not path_value:
        return path_value
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(base_dir, path_value)

# Create Flask application with necessary configs
def create_application():
    # Create the app through the factory function
    app = create_app()
    
    # Load connection configuration
    connection_config_path = os.environ.get('CONNECTION_CONFIG', 'connection.json')
    logger.info(f"Loading connection configuration from: {connection_config_path}")

    app_root = os.path.dirname(os.path.abspath(__file__))
    connection_config = {
        "host": "127.0.0.1",
        "port": 11111,
        "client_id": 1,
        "readonly": True,
        "portfolio_env": "SIMULATE",
        "security_firm": "FUTUSECURITIES",
        "account_id": "",
        "db_path": os.path.join(app_root, 'options.db'),
        "auto_launch_opend": False,
        "opend_path": ""
    }
    
    if os.path.exists(connection_config_path):
        try:
            with open(connection_config_path, 'r') as f:
                file_config = json.load(f)
                connection_config.update(file_config)
                logger.info(f"Loaded connection configuration from {connection_config_path}")
        except Exception as e:
            logger.error(f"Error loading connection configuration: {str(e)}")
    else:
        logger.warning(f"Connection configuration file {connection_config_path} not found, using defaults")

    apply_env_overrides(connection_config)

    db_path = _resolve_local_path(connection_config.get('db_path'), app_root)
    connection_config['db_path'] = db_path
    logger.info(f"Initializing database at {db_path}")
    options_db = OptionsDatabase(db_path)
    app.config['database'] = options_db

    # Store connection config in the app
    app.config['connection_config'] = connection_config
    logger.info(f"Using connection config: {connection_config}")

    @app.after_request
    def disable_static_asset_cache(response):
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response
    
    return app

# Create the application
app = create_application()

# Web routes
@app.route('/')
def index():
    """
    Render the dashboard page
    """
    logger.info("Rendering dashboard page")
    return render_template('dashboard.html')

@app.route('/portfolio')
def portfolio():
    """
    Render the portfolio page
    """
    logger.info("Rendering portfolio page")
    return render_template('portfolio.html')

@app.route('/options')
def options():
    """
    Temporarily redirect options page to home
    """
    logger.info("Options page accessed but currently unavailable - redirecting to home")
    return redirect(url_for('index'))

@app.route('/rollover')
def rollover():
    """
    Render the rollover page for options approaching strike price
    """
    logger.info("Rendering rollover page")
    return render_template('rollover.html')

@app.errorhandler(404)
def page_not_found(e):
    """
    Handle 404 errors
    """
    logger.warning(f"404 error: {request.path}")
    return render_template('error.html', error_code=404, message="Page not found"), 404

@app.errorhandler(500)
def server_error(e):
    """
    Handle 500 errors
    """
    logger.error(f"500 error: {str(e)}")
    return render_template('error.html', error_code=500, message="Server error"), 500

if __name__ == '__main__':
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 8000))
    
    # Run the application
    logger.info(f"Starting Flask development server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True) 
