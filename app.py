"""
Auto-Trader Web Application
Main entry point for the web application
"""

import os
import json
from dotenv import load_dotenv
load_dotenv()  # Load .env before any config is read
import atexit
from flask import render_template, request, redirect, url_for, jsonify, current_app
from api import create_app
from core.logging_config import get_logger
from db.database import OptionsDatabase
from config import apply_env_overrides
from api.services.iv_earnings_service import IVEarningsService
from core.background_manager import BackgroundTaskManager
from core.tasks import start_earnings_updater as _start_tasks, stop_all_tasks

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


# Create Flask application with necessary configs
def create_application():
    # Create the app through the factory function
    app = create_app()

    # Load connection configuration
    connection_config_path = os.environ.get('CONNECTION_CONFIG', 'connection.json')
    logger.info(f"Loading connection configuration from: {connection_config_path}")

    app_root = os.path.dirname(os.path.abspath(__file__))
    from config import DEFAULT_CONNECTION_CONFIG
    connection_config = dict(DEFAULT_CONNECTION_CONFIG)
    connection_config.update({
        "client_id": 1,
        "db_path": os.path.join(app_root, DEFAULT_CONNECTION_CONFIG.get('db_path', 'options.db')),
        "auto_launch_opend": False,
        "opend_path": ""
    })

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

    @app.context_processor
    def inject_screening_config():
        conn_config = current_app.config.get('connection_config', {})
        growth_mode = conn_config.get('growth_mode', {})
        screener_profile = growth_mode.get('screener_profile', {})
        return {
            'screening_config': {
                'growth_mode_enabled': bool(growth_mode.get('enabled', True)),
                'csp_default_otm_pct': screener_profile.get('csp_default_otm_pct', 10),
                'call_default_otm_pct': screener_profile.get('call_default_otm_pct', 10),
                'csp_min_dte': screener_profile.get('csp_min_dte', 30),
                'csp_max_dte': screener_profile.get('csp_max_dte', 45),
                'csp_preferred_dte': screener_profile.get('csp_preferred_dte', 37),
                'csp_min_otm_pct': screener_profile.get('csp_min_otm_pct', 5),
                'csp_max_otm_pct': screener_profile.get('csp_max_otm_pct', 15),
            }
        }

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

# Server-side safety check: block REAL + readonly=false at startup
connection_config = app.config.get('connection_config', {})
if connection_config.get('portfolio_env') == 'REAL' and not connection_config.get('readonly', True):
    confirm = os.environ.get('CONFIRM_LIVE_TRADING', '').strip().lower()
    if confirm not in {'1', 'true', 'yes', 'y', 'on'}:
        logger.critical(
            "STARTUP BLOCKED: portfolio_env=REAL with readonly=false requires "
            "CONFIRM_LIVE_TRADING=true env var. Falling back to SIMULATE."
        )
        connection_config['portfolio_env'] = 'SIMULATE'
        connection_config['readonly'] = True

# Start background earnings updater (runs every 6 hours)
try:
    _start_tasks(app, task_manager)
    # Start health monitor
    task_manager.start_health_monitor(interval=60)
    # Register stop signal for graceful shutdown
    atexit.register(lambda: stop_all_tasks(task_manager))
except Exception as e:
    logger.error(f"Failed to start earnings updater: {e}")

# Start evaluator/calibrator scheduler (daily evaluator, weekly calibration)
try:
    from core.scheduler import start_scheduler, stop_scheduler
    started = start_scheduler()
    if started:
        logger.info("Evaluator/calibrator scheduler started (this process owns the lock)")
    else:
        logger.info("Evaluator/calibrator scheduler skipped (another process owns the lock)")
    atexit.register(stop_scheduler)
except Exception as e:
    logger.error(f"Failed to start evaluator/calibrator scheduler: {e}")

# Web routes
@app.route('/')
def index():
    """
    Render the dashboard page
    """
    logger.info("Rendering dashboard page")
    return render_template('dashboard.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204

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
    from api.services.portfolio_service import PortfolioService

    db = current_app.config.get('database') or OptionsDatabase()
    service = IVEarningsService(db)
    portfolio = PortfolioService()

    # Get all active items from positions + watchlist
    positions = portfolio.get_positions() or []

    all_tickers = set()
    for p in positions:
        all_tickers.add(p.get('symbol'))

    try:
        from api.services.watchlist_manager import WatchlistManager
        connection_config = current_app.config.get('connection_config', {})
        wm = WatchlistManager(connection_config)
        growth_mode = connection_config.get('growth_mode', {})
        watchlist_tickers = [
            t.strip().upper()
            for t in wm.get_effective_watchlist(growth_mode_config=growth_mode)
            if t.strip()
        ]
        all_tickers.update(watchlist_tickers)
    except Exception:
        pass

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
