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
from datetime import datetime
from flask import Flask, current_app
from flask_cors import CORS
from core.logging_config import get_logger
from core.connection import probe_opend_status

# Configure logging
logger = get_logger('autotrader.api', 'api')

# Service registry for lazy initialization
# Maps service name -> factory function that creates the service
_service_registry = {}
# Maps service name -> singleton instance
_service_instances = {}


def register_service(name, factory):
    """
    Register a service factory for lazy initialization.

    Args:
        name: Service name (e.g., 'options', 'portfolio')
        factory: Callable that creates the service instance
    """
    _service_registry[name] = factory
    logger.debug(f"Registered service factory: {name}")


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
        raise ValueError(f"Unknown service: {name}")

    # Return cached instance if exists
    if name in _service_instances:
        return _service_instances[name]

    # Create new instance
    factory = _service_registry[name]
    instance = factory()
    _service_instances[name] = instance
    logger.debug(f"Created service instance: {name}")
    return instance


def clear_service_cache():
    """Clear all service instances (useful for testing)."""
    _service_instances.clear()


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
        LLM_ENABLED=os.environ.get('LLM_ENABLED', 'false'),
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
    from api.routes import portfolio, options, macro, system, llm
    app.register_blueprint(portfolio.bp)
    app.register_blueprint(options.bp)
    app.register_blueprint(macro.bp)
    app.register_blueprint(system.bp)
    app.register_blueprint(llm.bp)

    # Register new feature blueprints
    from api.routes import earnings, pop, risk, technical
    app.register_blueprint(earnings.bp)
    app.register_blueprint(pop.bp)
    app.register_blueprint(risk.bp)
    app.register_blueprint(technical.bp)

    # Extracted route modules (F008)
    from api.routes import roll_pressure, alerts
    app.register_blueprint(roll_pressure.bp)
    app.register_blueprint(alerts.bp)
    logger.info("Registered API blueprints")

    @app.route('/health')
    def health_check():
        logger.debug("Health check endpoint called")

        # Check tvscreener availability
        tvscreener_status = 'unknown'
        try:
            from api import get_service
            tvscreener = get_service('tvscreener')
            if tvscreener and tvscreener._ensure_initialized():
                tvscreener_status = 'available'
            else:
                tvscreener_status = 'unavailable'
        except Exception:
            tvscreener_status = 'error'

        return {
            'status': 'healthy',
            'tvscreener': tvscreener_status,
            'timestamp': datetime.now().isoformat()
        }

    @app.route('/api/system/opend-status')
    def opend_status():
        connection_config = current_app.config.get('connection_config', {})
        host = connection_config.get('host', '127.0.0.1')
        port = connection_config.get('port', 11111)
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
    from api.services.tvscreener_service import create_tvscreener_service

    # Register service factories (not instances yet)
    register_service('options', OptionsService)
    register_service('portfolio', PortfolioService)
    register_service('tvscreener', create_tvscreener_service)

    def _create_iv_earnings_service():
        from api.services.config import get_config
        from db.database import OptionsDatabase
        from api.services.iv_earnings_service import IVEarningsService
        db = OptionsDatabase(get_config().get('db_path'))
        return IVEarningsService(db)

    register_service('ivearnings', _create_iv_earnings_service)

    def _create_earnings_vol_signal_service():
        from flask import current_app
        from api.services.earnings_vol_service import EarningsVolSignalService
        return EarningsVolSignalService(
            config=current_app.config.get('connection_config', {}),
            iv_earnings_service=get_service('ivearnings'),
        )

    register_service('earnings_vol', _create_earnings_vol_signal_service)
