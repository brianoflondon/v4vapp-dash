import json
import logging
from datetime import UTC, datetime

from v4vapp_dash.logging.mylogger import MyJSONFormatter, human_readable_datetime_str


def test_format_basic_log_record() -> None:
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.created = datetime.now(tz=UTC).timestamp()

    formatted_message = MyJSONFormatter().format(record)
    log_dict = json.loads(formatted_message)

    assert log_dict["message"] == "Test message"
    assert "timestamp" in log_dict
    assert "human_time" in log_dict


def test_format_log_record_with_exception() -> None:
    try:
        raise ValueError("Test exception")
    except ValueError as ex:
        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=20,
            msg="Test message with exception",
            args=(),
            exc_info=(type(ex), ex, ex.__traceback__),
        )
        record.created = datetime.now(tz=UTC).timestamp()
        record.stack_info = "Stack info"
        record.unusual_attr = "Unusual attribute"

    formatted_message = MyJSONFormatter().format(record)
    log_dict = json.loads(formatted_message)

    assert log_dict["message"] == "Test message with exception"
    assert "timestamp" in log_dict
    assert "exc_info" in log_dict
    assert log_dict["unusual_attr"] == "Unusual attribute"


def test_format_log_record_with_custom_keys() -> None:
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=30,
        msg="Test message with custom keys",
        args=(),
        exc_info=None,
    )
    record.created = datetime.now(tz=UTC).timestamp()

    custom_keys = {"log_message": "message", "log_timestamp": "timestamp"}
    formatted_message = MyJSONFormatter(fmt_keys=custom_keys).format(record)
    log_dict = json.loads(formatted_message)

    assert log_dict["log_message"] == "Test message with custom keys"
    assert "log_timestamp" in log_dict


def test_format_includes_human_time_after_level_and_extras() -> None:
    record = logging.LogRecord(
        name="v4vapp_dash",
        level=logging.INFO,
        pathname=__file__,
        lineno=40,
        msg="request",
        args=(),
        exc_info=None,
    )
    record.created = datetime.now(tz=UTC).timestamp()
    record.invoice_id = "inv-1"
    record.duration_ms = 12.5

    fmt_keys = {
        "level": "levelname",
        "message": "message",
        "timestamp": "timestamp",
    }
    log_dict = json.loads(MyJSONFormatter(fmt_keys=fmt_keys).format(record))
    keys = list(log_dict.keys())
    assert log_dict["level"] == "INFO"
    assert log_dict["message"] == "request"
    assert log_dict["invoice_id"] == "inv-1"
    assert log_dict["duration_ms"] == 12.5
    assert keys[keys.index("level") + 1] == "human_time"


def test_human_readable_datetime_str() -> None:
    dt_obj = datetime(2022, 1, 1, 12, 30, 45, 123456)
    assert human_readable_datetime_str(dt_obj) == "12:30:45.123 Sat 01 Jan"
