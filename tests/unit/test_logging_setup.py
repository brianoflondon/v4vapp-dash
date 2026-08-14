import json
import logging
from pathlib import Path

import pytest

from v4vapp_dash.config import LoggingSettings, Settings, get_settings
from v4vapp_dash.logging import logger
from v4vapp_dash.logging import setup as logging_setup
from v4vapp_dash.logging.setup import reset_logging, setup_logging


def test_setup_logging_writes_json_line(tmp_path: Path) -> None:
    reset_logging()
    setup_logging(Settings(logging=LoggingSettings(log_folder=tmp_path)))
    logger.info("hello-json")
    for handler in logging.getLogger().handlers:
        handler.flush()

    jsonl = tmp_path / "v4vapp_dash.jsonl"
    assert jsonl.is_file()
    line = jsonl.read_text().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello-json"
    assert payload["logger"] == "v4vapp_dash"


def test_second_setup_logging_is_noop(tmp_path: Path) -> None:
    reset_logging()
    first = tmp_path / "first"
    second = tmp_path / "other"
    setup_logging(Settings(logging=LoggingSettings(log_folder=first)))
    assert logging_setup._configured is True
    setup_logging(Settings(logging=LoggingSettings(log_folder=second)))
    logger.info("only-once")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert (first / "v4vapp_dash.jsonl").is_file()
    assert not (second / "v4vapp_dash.jsonl").exists()


def test_missing_dictconfig_uses_fallback(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4VAPP_FORCE_CONSOLE_LOG", "1")
    reset_logging()
    setup_logging(
        Settings(
            logging=LoggingSettings(
                log_folder=tmp_path,
                log_config_file="does-not-exist.json",
            )
        )
    )
    logger.info("fallback-line")
    for handler in logging.getLogger().handlers:
        handler.flush()

    jsonl = tmp_path / "v4vapp_dash.jsonl"
    assert jsonl.is_file()
    assert "fallback-line" in jsonl.read_text()
    names = [getattr(h, "name", "") for h in logging.getLogger().handlers]
    assert "stdout_color" in names
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_scalar_logging_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGGING", "debug")
    settings = Settings()
    assert isinstance(settings.logging, LoggingSettings)
    assert settings.logging.console_log_level == "INFO"

    monkeypatch.setenv("LOGGING", "1")
    settings = Settings()
    assert isinstance(settings.logging, LoggingSettings)

    monkeypatch.delenv("LOGGING", raising=False)
    monkeypatch.setenv("LOGGING__CONSOLE_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    settings = Settings()
    assert settings.logging.console_log_level == "WARNING"

    monkeypatch.setenv("LOGGING", "debug")
    monkeypatch.setenv("LOGGING__CONSOLE_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("LOGGING__DEFAULT_LOG_LEVEL", "WARNING")
    settings = Settings()
    assert settings.logging.console_log_level == "ERROR"
    assert settings.logging.default_log_level == "WARNING"
