import datetime as dt
import json
import logging
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import override

LOG_RECORD_BUILTIN_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def human_readable_datetime_str(dt_obj: datetime) -> str:
    """
    Convert a datetime object to a human-readable string.

    Args:
        dt_obj (datetime): The datetime object to be converted.

    Returns:
        str: The formatted string representing the datetime.
    """
    ms = dt_obj.microsecond // 1000
    return f"{dt_obj:%H:%M:%S}.{ms:03d} {dt_obj:%a %d %b}"


def parse_log_level(level: str | int | None, fallback: int = logging.INFO) -> int:
    """
    Parse a logging level into its numeric value without using
    logging.getLevelName (which can return a string for unknown names).

    Accepts:
      - int -> returned unchanged
      - numeric string like "20" -> parsed to int
      - name like "INFO" or "debug" -> looked up in logging._nameToLevel
      - common synonym "WARN" is accepted for "WARNING"

    On unknown inputs, returns `fallback` (default: logging.INFO).
    """
    # Already numeric
    if isinstance(level, int):
        return level
    if level is None:
        return fallback

    s = str(level).strip()

    # Numeric string
    try:
        return int(s)
    except (ValueError, TypeError):
        pass

    name = s.upper()
    if name == "WARN":
        name = "WARNING"

    name_to_level = getattr(logging, "_nameToLevel", None)
    if name_to_level is not None and name in name_to_level:
        return name_to_level[name]

    # Unknown -> fallback
    return fallback


class MyJSONFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        fmt_keys: dict[str, str] | None = None,
    ):
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    @override
    def format(self, record: logging.LogRecord) -> str:
        def json_default(o):
            return _json_default(o)

        try:
            message = self._prepare_log_dict(record)
            return json.dumps(message, default=json_default)
        except Exception as e:
            print(f"Error formatting log record: {e}")
            return super().format(record)

    def _prepare_log_dict(self, record: logging.LogRecord):
        human_readable_str = human_readable_datetime_str(
            dt.datetime.fromtimestamp(record.created, tz=dt.UTC)
        )
        always_fields = {
            "message": record.getMessage(),
            "human_time": human_readable_str,
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
        }
        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: (
                msg_val
                if (msg_val := always_fields.pop(val, None)) is not None
                else getattr(record, val, None)
            )
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        for key, val in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_ATTRS:
                message[key] = val

        if "human_time" in message:
            human_time_value = message.pop("human_time")
            new_message = OrderedDict()
            inserted = False
            for k, v in message.items():
                new_message[k] = v
                if k == "level":
                    new_message["human_time"] = human_time_value
                    inserted = True
            if not inserted:
                new_message["human_time"] = human_time_value
            message = new_message
        return message


class ConsoleLogFilter(logging.Filter):
    """Allow records at or above the level set by setup_logging."""

    _cached_levelno: int = logging.INFO

    @classmethod
    def set_level(cls, level: str | int) -> None:
        cls._cached_levelno = parse_log_level(level, fallback=logging.INFO)

    @override
    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        return record.levelno >= ConsoleLogFilter._cached_levelno


IGNORE_REPORT_FIELDS = LOG_RECORD_BUILTIN_ATTRS | {
    "_redacted",
}


def _json_default(o):
    """Decimal / Decimal128 without raising InvalidOperation or OverflowError."""
    try:
        from bson.decimal128 import Decimal128  # type: ignore
    except Exception:  # pragma: no cover - environment may not have bson
        Decimal128 = None

    if Decimal128 is not None and isinstance(o, Decimal128):
        try:
            o = o.to_decimal()
        except Exception:
            return str(o)

    if isinstance(o, Decimal):
        try:
            if o.is_nan() or o.is_infinite():
                return str(o)
            f = float(o)
            import math

            if not math.isfinite(f):
                return str(o)
        except (InvalidOperation, OverflowError):
            return str(o)
        return round(f, 11)

    return str(o)


class AddJsonDataIndicatorFilter(logging.Filter):
    """Append extra field names to the console message, e.g. [invoice_id, duration_ms]."""

    @override
    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        extra_fields = set(record.__dict__.keys()) - IGNORE_REPORT_FIELDS
        if extra_fields:
            extra_text = " ["
            extra_text += ", ".join(f"{field}" for field in extra_fields)
            extra_text += "]"
            if hasattr(record, "msg") and isinstance(record.msg, str):
                record.msg += extra_text
            if hasattr(record, "message") and isinstance(record.message, str):
                record.message += extra_text
        return record
