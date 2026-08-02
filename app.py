"""
Auto-Trader Web Application
Main entry point for the web application
"""

import atexit
import json
import os

from dotenv import load_dotenv

load_dotenv()  # Load .env before any config is read

from flask import current_app, redirect, render_template, request, url_for

from api import create_app
from config import apply_env_overrides
from core.background_manager import BackgroundTaskManager
from core.logging_config import get_logger
from db.database import OptionsDatabase

# Configure logging
logger = get_logger("autotrader.app", "api")

# Global task manager for background tasks
task_manager = BackgroundTaskManager()

# Module-level app handle. Built lazily by ensure_app() so that importing
# this module never opens the database, starts threads, or writes to disk.
_app = None

__all__ = ["create_application", "ensure_app", "start_runtime"]


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
    connection_config_path = os.environ.get("CONNECTION_CONFIG", "connection.json")
    logger.info(f"Loading connection configuration from: {connection_config_path}")

    app_root = os.path.dirname(os.path.abspath(__file__))
    from config import DEFAULT_CONNECTION_CONFIG

    connection_config = dict(DEFAULT_CONNECTION_CONFIG)
    connection_config.update(
        {
            "client_id": 1,
            "db_path": os.path.join(app_root, DEFAULT_CONNECTION_CONFIG.get("db_path", "options.db")),
            "auto_launch_opend": False,
            "opend_path": "",
        }
    )

    if os.path.exists(connection_config_path):
        try:
            with open(connection_config_path, "r") as f:
                file_config = json.load(f)
                connection_config.update(file_config)
                logger.info(f"Loaded connection configuration from {connection_config_path}")
        except Exception as e:
            logger.error(f"Error loading connection configuration: {str(e)}")
    else:
        logger.warning(f"Connection configuration file {connection_config_path} not found, using defaults")

    apply_env_overrides(connection_config)

    db_path = _resolve_local_path(connection_config.get("db_path"), app_root)
    connection_config["db_path"] = db_path
    logger.info(f"Initializing database at {db_path}")
    options_db = OptionsDatabase(db_path)
    app.config["database"] = options_db

    # Store connection config in the app
    app.config["connection_config"] = connection_config
    logger.info(f"Using connection config: {connection_config}")

    @app.context_processor
    def inject_screening_config():
        conn_config = current_app.config.get("connection_config", {})
        growth_mode = conn_config.get("growth_mode", {})
        screener_profile = growth_mode.get("screener_profile", {})
        return {
            "screening_config": {
                "growth_mode_enabled": bool(growth_mode.get("enabled", True)),
                "csp_default_otm_pct": screener_profile.get("csp_default_otm_pct", 10),
                "call_default_otm_pct": screener_profile.get("call_default_otm_pct", 10),
                "csp_min_dte": screener_profile.get("csp_min_dte", 30),
                "csp_max_dte": screener_profile.get("csp_max_dte", 45),
                "csp_preferred_dte": screener_profile.get("csp_preferred_dte", 37),
                "csp_min_otm_pct": screener_profile.get("csp_min_otm_pct", 5),
                "csp_max_otm_pct": screener_profile.get("csp_max_otm_pct", 15),
            }
        }

    @app.after_request
    def disable_static_asset_cache(response):
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return app


def register_web_routes(app):
    """Register page routes on an application instance (called once)."""

    @app.route("/")
    def index():
        """Render the dashboard page"""
        logger.info("Rendering dashboard page")
        return render_template("dashboard.html")

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    @app.route("/portfolio")
    def portfolio():
        """Render the portfolio page"""
        logger.info("Rendering portfolio page")
        return render_template("portfolio.html")

    @app.route("/options")
    def options():
        """Temporarily redirect options page to home"""
        logger.info("Options page accessed but currently unavailable - redirecting to home")
        return redirect(url_for("index"))

    @app.route("/rollover")
    def rollover():
        """Render the rollover page for options approaching strike price"""
        logger.info("Rendering rollover page")
        return render_template("rollover.html")

    @app.errorhandler(404)
    def page_not_found(e):
        """Handle 404 errors"""
        logger.warning(f"404 error: {request.path}")
        return render_template("error.html", error_code=404, message="Page not found"), 404

    @app.errorhandler(500)
    def server_error(e):
        """Handle 500 errors"""
        logger.error(f"500 error: {str(e)}")
        return render_template("error.html", error_code=500, message="Server error"), 500


def ensure_app():
    """Build (once) and return the application. Explicit entry points only."""
    global _app
    if _app is None:
        _app = create_application()
        register_web_routes(_app)
    return _app


def start_runtime(app):
    """Start runtime services (health monitor).

    Called only from explicit entry points (run_api.py, python app.py).
    Importing this module or ensure_app() must NOT start threads or
    background work; only start_runtime() does.
    """
    # Start health monitor
    try:
        task_manager.start_health_monitor(interval=60)
        atexit.register(task_manager.stop_health_monitor)
    except Exception as e:
        logger.error(f"Failed to start health monitor: {e}")


if __name__ == "__main__":
    application = ensure_app()
    start_runtime(application)
    port = int(os.environ.get("PORT", 8000))

    # Run the application (loopback only; single-user local app)
    logger.info(f"Starting Flask development server on port {port}")
    application.run(host="127.0.0.1", port=port, debug=False)
