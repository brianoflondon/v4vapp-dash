from __future__ import annotations

import copy
import json
import logging
import logging.config
import logging.handlers
import os
import sys
from pathlib import Path

from v4vapp_dash.config import Settings
from v4vapp_dash.logging.mylogger import (
    AddJsonDataIndicatorFilter,
    ConsoleLogFilter,
    parse_log_level,
)
from v4vapp_dash.logging.namer import make_rotation_namer
from v4vapp_dash.logging.redact import SecretRedactFilter

_configured = False

_STDOUT_HANDLER_NAME = "stdout_color"

_FALLBACK_DICT_CONFIG: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": (
                "%(asctime)s.%(msecs)03d %(levelname)-8s %(module)-22s %(lineno)6d : %(message)s"
            ),
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        },
        "json": {
            "()": "v4vapp_dash.logging.mylogger.MyJSONFormatter",
            "fmt_keys": {
                "level": "levelname",
                "message": "message",
                "timestamp": "timestamp",
                "logger": "name",
                "module": "module",
                "function": "funcName",
                "line": "lineno",
                "thread_name": "threadName",
            },
        },
    },
    "filters": {
        "redact": {
            "()": "v4vapp_dash.logging.redact.SecretRedactFilter",
        }
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
            "formatter": "simple",
            "stream": "ext://sys.stderr",
            "filters": ["redact"],
        },
        "file_json": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "json",
            "filename": "logs/v4vapp_dash.jsonl",
            "maxBytes": 2000000,
            "backupCount": 10,
            "filters": ["redact"],
        },
    },
    "loggers": {
        "root": {
            "level": "DEBUG",
            "handlers": ["stderr", "file_json"],
        }
    },
}


def reset_logging() -> None:
    """Close root handlers and allow setup_logging to run again. Tests only."""
    global _configured
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    ConsoleLogFilter.set_level(logging.INFO)
    _configured = False


def _load_dict_config(log_config_file: str) -> dict:
    config_path = Path("config") / "logging" / log_config_file
    try:
        with config_path.open() as f_in:
            return json.load(f_in)
    except (FileNotFoundError, IsADirectoryError, OSError) as ex:
        print(f"Logging config file not found: {ex}", file=sys.stderr)
        return copy.deepcopy(_FALLBACK_DICT_CONFIG)


def _ensure_redact_filter(handler: logging.Handler) -> None:
    for existing in handler.filters:
        if isinstance(existing, SecretRedactFilter):
            return
    handler.addFilter(SecretRedactFilter())


def _root_has_stdout_handler() -> bool:
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            stream = getattr(handler, "stream", None)
            if stream is sys.stdout:
                return True
    return False


def _install_stdout_color(format_str: str) -> None:
    root_logger = logging.getLogger()
    force_console = os.getenv("V4VAPP_FORCE_CONSOLE_LOG") == "1"
    already_named = any(
        getattr(h, "name", "") == _STDOUT_HANDLER_NAME for h in root_logger.handlers
    )
    if already_named:
        return
    if not force_console and _root_has_stdout_handler():
        return

    try:
        import colorlog

        handler: logging.Handler = colorlog.StreamHandler(stream=sys.stdout)
        handler.set_name(_STDOUT_HANDLER_NAME)
        handler.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s" + format_str,
                datefmt="%m-%dT%H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "blue",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "red,bg_white",
                },
            )
        )
    except Exception:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.set_name(_STDOUT_HANDLER_NAME)
        handler.setFormatter(logging.Formatter(format_str, datefmt="%Y-%m-%dT%H:%M:%S%z"))

    handler.addFilter(SecretRedactFilter())
    handler.addFilter(ConsoleLogFilter())
    handler.addFilter(AddJsonDataIndicatorFilter())
    root_logger.addHandler(handler)


def setup_logging(settings: Settings) -> None:
    global _configured
    if _configured:
        return

    config = _load_dict_config(settings.logging.log_config_file)
    log_folder = Path(settings.logging.log_folder)
    try:
        config["handlers"]["file_json"]["filename"] = str(log_folder / "v4vapp_dash.jsonl")
    except KeyError as ex:
        print(f"KeyError in logging config no logfile set: {ex}", file=sys.stderr)

    log_folder.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(config)

    for logger_name, level in (settings.logging.log_levels or {}).items():
        logging.getLogger(logger_name).setLevel(level)

    rotation_folder_flag = bool(settings.logging.rotation_folder)

    def _attach_namer(handler: logging.Handler) -> None:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.namer = make_rotation_namer(
                handler, rotation_folder=rotation_folder_flag, min_width=3
            )

    file_json_handler = logging.getHandlerByName("file_json")
    if file_json_handler is not None:
        _attach_namer(file_json_handler)
    for logger_obj in logging.root.manager.loggerDict.values():
        if isinstance(logger_obj, logging.Logger):
            for handler in logger_obj.handlers:
                _attach_namer(handler)
    for handler in logging.getLogger().handlers:
        _attach_namer(handler)

    try:
        format_str = config["formatters"]["simple"]["format"]
    except KeyError:
        format_str = (
            "%(asctime)s.%(msecs)03d %(levelname)-8s %(module)-22s %(lineno)6d : %(message)s"
        )

    _install_stdout_color(format_str)

    for name in ("file_json", "stderr"):
        handler = logging.getHandlerByName(name)
        if handler is not None:
            _ensure_redact_filter(handler)

    logging.getLogger().setLevel(
        parse_log_level(settings.logging.default_log_level, fallback=logging.DEBUG)
    )
    ConsoleLogFilter.set_level(settings.logging.console_log_level)
    logging.getLogger("v4vapp_dash").propagate = True
    _configured = True
