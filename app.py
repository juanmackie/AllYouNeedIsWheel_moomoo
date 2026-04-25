"""
Auto-Trader Web Application
Main entry point for the web application
"""

import os
import json
import threading
from dotenv import load_dotenv
load_dotenv()  # Load .env before any config is read
import time
import atexit
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, current_app
from api import create_app
from core.logging_config import get_logger
from db.database import OptionsDatabase
from core.connection import MoomooConnection
from config import apply_env_overrides
from api.services.iv_earnings_service import IVEarningsService
from core.background_manager import BackgroundTaskManager, TaskConfig

# Configure logging
logger = get_logger('autotrader.app', 'api')

# Global task manager for background tasks
task_manager = BackgroundTaskManager()


def _resolve_local_path(path_value, base_dir):
    if not path_value:
        return path_value
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(base_dir, path_value)


def _earnings_worker(app):
    """
    Background worker to fetch earnings data.
    This function is called periodically by the task manager.
    """
    with app.app_context():
        db = app.config.get("database") or OptionsDatabase()
        service = IVEarningsService(db)
        
        logger.info("Earnings updater worker executing")
        
        try:
            # Get tickers from recent orders
            recent_orders = db.get_orders(limit=100)
            order_tickers = list(set(order['ticker'] for order in recent_orders if order.get('ticker')))
            
            # Also get tickers from portfolio positions
            position_tickers = []
            try:
                import api
                portfolio_service = api.get_service('portfolio')
                portfolio_data = portfolio_service.get_positions()
                if portfolio_data:
                    position_tickers = [
                        pos.get('symbol') 
                        for pos in portfolio_data 
                        if pos.get('security_type') == 'STK'
                    ]
            except Exception as e:
                logger.warning(f"Could not fetch portfolio positions for earnings: {e}")
            
            # Combine and deduplicate tickers
            all_tickers = list(set(order_tickers + position_tickers))
            
            if all_tickers:
                logger.info(f"Updating earnings for {len(all_tickers)} tickers (orders + positions)")
                result = service.batch_update_earnings(all_tickers)
                logger.info(f"Earnings update complete: {result['successful']} successful, {result['failed']} failed")
            
            # Purge old IV data
            service.purge_old_data()
            
        except Exception as e:
            logger.error(f"Error in earnings updater: {e}")
            raise  # Re-raise to trigger restart if configured


def start_earnings_updater(app):
    """
    Start background earnings updater using BackgroundTaskManager.
    """
    # Register the earnings task
    task_manager.register(TaskConfig(
        name='earnings_updater',
        worker_fn=lambda: _earnings_worker(app),
        restart_on_failure=True,
        max_restart_attempts=5,
        restart_delay_seconds=60,
    ))
    
    # Start the task
    task_manager.start('earnings_updater')
    logger.info("Earnings updater background task started")


def stop_earnings_updater():
    """Stop all background tasks."""
    task_manager.stop_all()
    logger.info("All background tasks stopped")


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

# Start background earnings updater (runs every 6 hours)
try:
    start_earnings_updater(app)
    # Start health monitor
    task_manager.start_health_monitor(interval=60)
    # Register stop signal for graceful shutdown
    atexit.register(stop_earnings_updater)
except Exception as e:
    logger.error(f"Failed to start earnings updater: {e}")

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

@app.route('/api/earnings/status')
def earnings_status():
    """
    Get earnings updater status and cache statistics
    """
    from api.services.iv_earnings_service import IVEarningsService
    db = current_app.config.get('database') or OptionsDatabase()
    service = IVEarningsService(db)
    
    # Check task status using the task manager
    earnings_task_status = task_manager.get_status('earnings_updater')
    is_running = earnings_task_status and earnings_task_status.get('running', False)
    
    return jsonify({
        'status': 'running' if is_running else 'stopped',
        'cache_stats': service.get_cache_stats()
    })

@app.route('/api/earnings/update/<ticker>')
def update_single_earnings(ticker):
    """
    Manually update earnings for a single ticker
    """
    from api.services.iv_earnings_service import IVEarningsService
    db = current_app.config.get('database') or OptionsDatabase()
    service = IVEarningsService(db)
    
    success = service.update_earnings_data(ticker)
    info = service.get_earnings_info(ticker)
    
    return jsonify({
        'success': success,
        'ticker': ticker,
        'earnings_info': info
    })

@app.route('/api/earnings/refresh', methods=['POST'])
def refresh_all_earnings():
    """
    Trigger a global update for all active symbols in background
    """
    from api.services.iv_earnings_service import IVEarningsService
    from api.services.portfolio_service import PortfolioService
    
    db = current_app.config.get('database') or OptionsDatabase()
    service = IVEarningsService(db)
    portfolio = PortfolioService()
    
    # Get all active items
    positions = portfolio.get_positions() or []
    recent_orders = db.get_orders(limit=100) or []
    
    all_tickers = set()
    for p in positions:
        all_tickers.add(p.get('symbol'))
        
    for o in recent_orders:
        if o.get('ticker'):
            all_tickers.add(o.get('ticker'))
            
    if not all_tickers:
        return jsonify({'success': True, 'updated': 0, 'message': 'No active symbols found'})

    # Update in foreground for the API response, or just trigger?
    # For better UX, let's update a few or just start a thread.
    # Actually, we can just run it synchronously if it's not too many.
    result = service.batch_update_earnings(list(all_tickers))
    
    return jsonify({
        'success': True,
        'updated_count': result['successful'],
        'failed_count': result['failed'],
        'total_attempted': len(all_tickers)
    })

@app.route('/api/earnings/pending')
def get_pending_earnings():
    """
    Get all tickers with pending earnings in the next 7 days
    """
    db = current_app.config.get('database') or OptionsDatabase()
    pending = db.get_pending_earnings(days_threshold=7)
    
    return jsonify({
        'count': len(pending),
        'tickers': pending
    })

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
