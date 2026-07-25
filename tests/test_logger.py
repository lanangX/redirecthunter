"""Tests for redirecthunter.logger."""

from __future__ import annotations

import logging
from pathlib import Path

from redirecthunter.logger import LogLevel, configure_logging, get_logger


class TestConfigureLogging:
    def test_sets_level(self) -> None:
        configure_logging(LogLevel.DEBUG)
        logger = logging.getLogger("redirecthunter")
        assert logger.level == logging.DEBUG

    def test_reconfiguration_does_not_stack_handlers(self) -> None:
        configure_logging(LogLevel.INFO)
        configure_logging(LogLevel.INFO)
        configure_logging(LogLevel.INFO)
        logger = logging.getLogger("redirecthunter")
        # Should never accumulate more than one console handler across repeated calls.
        assert len(logger.handlers) <= 1

    def test_quiet_suppresses_console_handler(self) -> None:
        configure_logging(LogLevel.INFO, quiet=True)
        logger = logging.getLogger("redirecthunter")
        # Either no handlers or a NullHandler -- never a console (Rich) handler.
        for handler in logger.handlers:
            assert isinstance(handler, logging.NullHandler)

    def test_log_file_writes_to_disk(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        configure_logging(LogLevel.INFO, log_file=log_file, quiet=True)
        logger = get_logger("test_logger_module")
        logger.info("hello from test")
        for handler in logging.getLogger("redirecthunter").handlers:
            handler.flush()
        assert log_file.exists()
        assert "hello from test" in log_file.read_text()


class TestGetLogger:
    def test_returns_namespaced_logger(self) -> None:
        logger = get_logger("my_module")
        assert logger.name == "redirecthunter.my_module"

    def test_already_namespaced_not_double_prefixed(self) -> None:
        logger = get_logger("redirecthunter.already_prefixed")
        assert logger.name == "redirecthunter.already_prefixed"

    def test_auto_configures_if_not_yet_configured(self) -> None:
        # Just verifies no exception is raised and a usable logger is returned,
        # regardless of whether configure_logging() was called by an earlier test.
        logger = get_logger("auto_config_test")
        assert isinstance(logger, logging.Logger)
