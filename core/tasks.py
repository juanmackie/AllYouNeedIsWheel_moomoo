"""
Background task worker definitions.

Extracted from app.py to keep the application entry point focused on
configuration and routing.  All periodic/background work lives here.
"""

import logging

logger = logging.getLogger('autotrader.tasks')


def create_earnings_worker(app):
    """Return a callable that runs the earnings-update cycle."""
    def _earnings_worker():
        from db.database import OptionsDatabase
        from api.services.iv_earnings_service import IVEarningsService

        with app.app_context():
            db = app.config.get("database") or OptionsDatabase()
            service = IVEarningsService(db)

            logger.info("Earnings updater worker executing")

            try:
                recent_orders = db.get_orders(limit=100)
                order_tickers = list(set(
                    order['ticker'] for order in recent_orders if order.get('ticker')
                ))

                position_tickers = []
                try:
                    import api
                    ps = api.get_service('portfolio')
                    data = ps.get_positions()
                    if data:
                        position_tickers = [
                            p.get('symbol') for p in data
                            if p.get('security_type') == 'STK'
                        ]
                except Exception as e:
                    logger.warning(f"Could not fetch portfolio positions: {e}")

                watchlist_tickers = []
                try:
                    from api.services.watchlist_manager import WatchlistManager
                    connection_config = app.config.get('connection_config', {})
                    wm = WatchlistManager(connection_config)
                    watchlist_tickers = [t.strip().upper() for t in wm.get_effective_watchlist() if t.strip()]
                except Exception as e:
                    logger.debug(f"Could not fetch effective watchlist: {e}")

                all_tickers = list(set(order_tickers + position_tickers + watchlist_tickers))
                if all_tickers:
                    logger.info(f"Updating earnings for {len(all_tickers)} tickers")
                    result = service.batch_update_earnings(all_tickers)
                    n_ok = result['successful']
                    n_fail = result['failed']
                    logger.info(f"Earnings update: {n_ok} ok, {n_fail} failed")

                service.purge_old_data()
            except Exception as e:
                logger.error(f"Earnings worker error: {e}")
                raise

    return _earnings_worker


def start_earnings_updater(app, task_manager):
    """Register and start the earnings-updater background task."""
    from core.background_manager import TaskConfig
    worker = create_earnings_worker(app)
    task_manager.register(TaskConfig(
        name='earnings_updater',
        worker_fn=worker,
        restart_on_failure=True,
        max_restart_attempts=5,
        restart_delay_seconds=60,
    ))
    task_manager.start('earnings_updater')
    logger.info("Earnings updater background task started")


def stop_all_tasks(task_manager):
    """Stop all background tasks."""
    task_manager.stop_all()
    logger.info("All background tasks stopped")
