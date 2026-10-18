"""Logging sink tests: service/core records must reach the api log file."""

import logging

import pytest

from core import logging_config


@pytest.fixture(autouse=True)
def _drop_leaked_root_handlers():
    """Restore the root logger after each test so other tests see no sink."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, logging_config._ROOT_HANDLER_TAG, None):
            root.removeHandler(handler)
            handler.close()


def _isolate_root(tmp_path, monkeypatch):
    """Point log output at tmp_path and drop handlers from earlier tests."""
    monkeypatch.setattr(logging_config, "LOGS_DIR", str(tmp_path))
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, logging_config._ROOT_HANDLER_TAG, None):
            root.removeHandler(handler)
            handler.close()
    return root


def _flush_all():
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_service_logger_record_reaches_the_api_log_file(tmp_path, monkeypatch):
    _isolate_root(tmp_path, monkeypatch)
    logging_config.configure_root_logging("api")

    logging.getLogger("api.services.recommendations").info("[TIMING] Contract discovery: 208.07s")
    _flush_all()

    log_file = next((tmp_path / "api").glob("api_*.log"))
    content = log_file.read_text(encoding="utf-8")
    assert "[TIMING] Contract discovery: 208.07s" in content
    assert "api.services.recommendations" in content


def test_refresh_failure_record_reaches_the_api_log_file(tmp_path, monkeypatch):
    _isolate_root(tmp_path, monkeypatch)
    logging_config.configure_root_logging("api")

    logging.getLogger("core.wheel_runner").warning("Wheel refresh failed")
    _flush_all()

    log_file = next((tmp_path / "api").glob("api_*.log"))
    assert "Wheel refresh failed" in log_file.read_text(encoding="utf-8")


def test_root_handler_is_added_once_per_file(tmp_path, monkeypatch):
    _isolate_root(tmp_path, monkeypatch)

    logging_config.configure_root_logging("api")
    logging_config.configure_root_logging("api")

    tagged = [h for h in logging.getLogger().handlers if getattr(h, logging_config._ROOT_HANDLER_TAG, None)]
    assert len(tagged) == 1


def test_configured_logger_writes_each_record_once(tmp_path, monkeypatch):
    _isolate_root(tmp_path, monkeypatch)
    logging_config.configure_root_logging("api")
    logger = logging_config.get_logger("ayniwheel.test_once", "api")

    logger.info("single record")
    _flush_all()

    log_file = next((tmp_path / "api").glob("api_*.log"))
    assert log_file.read_text(encoding="utf-8").count("single record") == 1
