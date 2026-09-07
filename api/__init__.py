"""
Auto-Trader API
Flask application initialization and configuration.

Import hierarchy (to avoid circular imports):
- core (no dependencies)
- db (depends on core)
- api.services (depends on core, db)
- api.routes (depends on api.services)
- api (depends on all above)

Services are lazily initialized after app creation to prevent circular import issues.
"""

import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from flask import Flask, current_app, g, jsonify, request

from core.context_factory import probe_opend_status
from core.logging_config import get_logger

# Configure logging
logger = get_logger("ayniwheel.api", "api")

# Service registry for lazy initialization
# Maps service name -> factory function that creates the service. The registry is
# process-global by design: factories are pure constructors shared across apps.
_service_registry = {}
# Serializes lazy construction so concurrent first access builds each service once.
_service_construction_lock = threading.Lock()
# Fallback instance store used when no Flask app context is active (tests/scripts).
# Inside a request context, instances live on current_app.extensions (per-app), so
# two apps built with different configs (e.g. distinct test databases) never share
# a service instantiated under another app's context.
_SERVICE_EXT_KEY = "ayniwheel_service_instances"
_service_instances_fallback = {}


# --- Loopback HTTP trust boundary (S01) ---
# Single-user loopback app. The server binds to 127.0.0.1, but the HTTP layer must
# also reject cross-origin requests and invalid Host headers so a page served from
# an untrusted site cannot drive state-changing routes (same-origin-policy-defying
# form POSTs / DNS-rebinding style Host tricks).
_STATE_CHANGING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _loopback_hostname(host: str) -> bool:
    """True when a bare host (no port) is a loopback address."""
    host = (host or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "::1", "::ffff:127.0.0.1"):
        return True
    if host.startswith("127.") and host.count(".") == 3:
        try:
            return all(0 <= int(octet) <= 255 for octet in host.split("."))
        except ValueError:
            return False
    return False


def _loopback_netloc(netloc: str) -> bool:
    """True when a Host header / Origin netloc (possibly with port) is loopback."""
    netloc = (netloc or "").strip()
    if not netloc:
        return False
    if netloc.startswith("["):
        host = netloc.split("]", 1)[0].lstrip("[")
    else:
        host = netloc.rsplit(":", 1)[0]
    return _loopback_hostname(host)


def is_loopback_host(host_header: str) -> bool:
    """Validate a raw Host header value (e.g. ``127.0.0.1:8000``)."""
    return _loopback_netloc(host_header)


def is_loopback_origin(origin_header: str) -> bool:
    """Validate an Origin header: http(s) and a loopback authority only."""
    origin = (origin_header or "").strip()
    if not origin:
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.username or parts.password:
        return False
    return _loopback_netloc(parts.netloc)


def _trust_boundary_denied(reason: str):
    logger.warning("Rejected request outside loopback trust boundary: %s", reason)
    payload = {
        "success": False,
        "error": "Request rejected: connection must originate from a loopback origin.",
        "error_code": "loopback_trust_boundary",
        "reason": reason,
    }
    return (jsonify(payload), 403)


def _resolve_secret_key(config=None):
    config = config or {}
    secret_key = config.get("SECRET_KEY") or os.environ.get("SECRET_KEY")
    if secret_key and secret_key != "dev":
        return secret_key

    env_name = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    in_dev_or_test = bool(
        config.get("TESTING")
        or config.get("DEBUG")
        or env_name in {"dev", "development", "test", "testing"}
        or os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    )

    if secret_key == "dev" and not in_dev_or_test:
        logger.warning("Ignoring insecure default SECRET_KEY=dev outside dev/test")
    elif not secret_key and not in_dev_or_test:
        logger.warning("SECRET_KEY was not set; using an ephemeral fallback secret")

    if in_dev_or_test and secret_key == "dev":
        return secret_key

    return secrets.token_urlsafe(32)


def register_service(name, factory):
    """
    Register a service factory for lazy initialization.

    Args:
        name: Service name (e.g., 'options', 'portfolio')
        factory: Callable that creates the service instance
    """
    _service_registry[name] = factory
    logger.debug(f"Registered service factory: {name}")


def _get_service_store():
    """Return the app-scoped instance dict, falling back to a module store when no
    Flask app context is active (e.g. clear_service_cache() invoked from a script)."""
    try:
        return current_app.extensions.setdefault(_SERVICE_EXT_KEY, {})
    except RuntimeError:
        return _service_instances_fallback


def get_service(name):
    """
    Get or create a registered service.

    Args:
        name: Service name

    Returns:
        Service instance

    Raises:
        ValueError: If service is not registered
    """
    if name not in _service_registry:
        logger.error(f"Unknown service requested: {name}")
        raise ValueError(f"Unknown service: {name}")

    store = _get_service_store()
    # Serialize lazy construction so concurrent first access builds each service once.
    with _service_construction_lock:
        if name in store:
            return store[name]
        instance = _service_registry[name]()
        store[name] = instance
    logger.debug(f"Created service instance: {name}")
    return instance


def clear_service_cache():
    """Clear service instances (useful for testing). Works inside or outside a
    request context: clears the active app's store and the fallback store."""
    try:
        current_app.extensions.pop(_SERVICE_EXT_KEY, None)
    except RuntimeError:
        pass
    _service_instances_fallback.clear()


def create_app(config=None):
    """
    Create and configure the Flask application.

    Args:
        config (dict, optional): Configuration dictionary

    Returns:
        Flask: Configured Flask application
    """
    logger.info("Creating API application")
    app = Flask(__name__, static_folder="../frontend/static", template_folder="../frontend/templates")

    # Single-user loopback app: same-origin templates/JS only, no CORS.
    # Default configuration
    app.config.from_mapping(
        SECRET_KEY=_resolve_secret_key(config),
        DATABASE="sqlite:///:memory:",
    )

    # Override with passed config
    if config:
        app.config.update(config)
        logger.debug("Applied custom configuration")

    # Register services AFTER app is created
    # This prevents circular imports: app -> routes -> services -> app
    logger.info("Registering service factories")
    _register_services()

    # Register blueprints
    from api.routes import options, portfolio

    app.register_blueprint(portfolio.bp)
    app.register_blueprint(options.bp)

    # Earnings calendar/IV (wheel earnings gate)
    from api.routes import earnings

    app.register_blueprint(earnings.bp)

    # Settings (persisted wheel preset)
    from api.routes import settings

    app.register_blueprint(settings.bp)

    # Watchlist (merged union with origins)
    from api.routes import watchlist

    app.register_blueprint(watchlist.bp)

    # Wheel run (immutable snapshots + refresh attempts)
    from api.routes import run

    app.register_blueprint(run.bp)

    # Extracted route modules (F008)
    from api.routes import alerts, roll_pressure

    app.register_blueprint(roll_pressure.bp)
    app.register_blueprint(alerts.bp)

    # Wheel Scan Ledger
    from api.routes import ledger

    app.register_blueprint(ledger.bp)
    logger.info("Registered API blueprints")

    @app.before_request
    def enforce_loopback_trust_boundary():
        # S01: reject invalid Host headers and cross-origin state-changing requests.
        # Healthy loopback requests (browser same-origin fetches, curl, Python clients)
        # carry a loopback Host; cross-origin browser requests carry a non-loopback
        # Origin on state-changing methods. Requests with no Origin header (non-browser
        # local clients) are preserved.
        if not is_loopback_host(request.host):
            return _trust_boundary_denied("invalid_host")
        if request.method in _STATE_CHANGING_METHODS:
            origin = request.headers.get("Origin")
            if origin and not is_loopback_origin(origin):
                return _trust_boundary_denied("disallowed_origin")
        return None

    @app.before_request
    def log_request_start():
        g.request_started_at = time.time()
        g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))

    @app.after_request
    def log_request_end(response):
        started_at = getattr(g, "request_started_at", None)
        elapsed_ms = int((time.time() - started_at) * 1000) if started_at else None
        request_id = getattr(g, "request_id", "")
        if request.path.startswith("/static/"):
            response.headers.setdefault("X-Request-Id", request_id)
            return response

        if elapsed_ms is None:
            logger.info(
                "request completed method=%s path=%s status=%s request_id=%s",
                request.method,
                request.path,
                response.status_code,
                request_id,
            )
        else:
            logger.info(
                "request completed method=%s path=%s status=%s duration_ms=%s request_id=%s",
                request.method,
                request.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
        response.headers.setdefault("X-Request-Id", request_id)
        return response

    @app.route("/health")
    def health_check():
        logger.debug("Health check endpoint called")

        database_status = "unknown"
        try:
            database = current_app.config.get("database")
            if database is not None:
                with sqlite3.connect(str(database.db_path)) as conn:
                    conn.execute("SELECT 1")
                database_status = "available"
            else:
                database_status = "unavailable"
        except Exception as exc:
            logger.warning("Health check database probe failed: %s", exc, exc_info=True)
            database_status = "error"

        try:
            opend_status = probe_opend_status(
                host=current_app.config.get("connection_config", {}).get("host", "127.0.0.1"),
                port=current_app.config.get("connection_config", {}).get("port", 11111),
            )
        except Exception as exc:
            logger.warning("Health check OpenD probe failed: %s", exc, exc_info=True)
            opend_status = {"status": "error"}

        opend_state = opend_status.get("status", "unknown")
        overall_status = "healthy" if database_status == "available" and opend_state == "connected" else "degraded"
        return {
            "status": overall_status,
            "database": database_status,
            "opend": opend_state,
            "timestamp": datetime.now().isoformat(),
        }

    @app.route("/api/system/opend-status")
    def opend_status():
        connection_config = current_app.config.get("connection_config", {})
        host = connection_config.get("host", "127.0.0.1")
        port = connection_config.get("port", 11111)
        return probe_opend_status(host=host, port=port)

    logger.info("API application created successfully")
    return app


def _register_services():
    """
    Register all service factories.

    This is called after the app is created to avoid circular imports.
    Services are created lazily when first accessed via get_service().
    """
    # Import services here to avoid circular imports at module level
    from api.services.options_service import OptionsService
    from api.services.portfolio_service import PortfolioService

    # Register service factories (not instances yet)
    register_service("options", OptionsService)
    register_service("portfolio", PortfolioService)

    def _create_iv_earnings_service():
        from api.services.config import get_config
        from api.services.iv_earnings_service import IVEarningsService
        from db.database import OptionsDatabase

        db = OptionsDatabase(get_config().get("db_path"))
        return IVEarningsService(db)

    register_service("ivearnings", _create_iv_earnings_service)

    def _create_wheel_runner():
        from flask import current_app

        from api.services.roll_diagnostics import build_roll_decisions
        from core.wheel_runner import WheelRunner

        config = current_app.config.get("connection_config", {})
        return WheelRunner(
            db=current_app.config.get("database"),
            options_service=get_service("options"),
            config=config,
            roll_diagnostics_provider=build_roll_decisions,
        )

    register_service("wheel_runner", _create_wheel_runner)
