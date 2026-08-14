from decimal import Decimal

from v4vapp_dash.logging.mylogger import _json_default

try:
    from bson.decimal128 import Decimal128
except Exception:  # pragma: no cover - bson may not be available in all environments
    Decimal128 = None


def test_decimal_normal() -> None:
    d = Decimal("12345.67890123456789")
    out = _json_default(d)
    assert isinstance(out, float)


def test_decimal_nan_and_inf() -> None:
    assert _json_default(Decimal("NaN")) == "NaN"
    assert _json_default(Decimal("sNaN")) == "sNaN"
    assert _json_default(Decimal("Infinity")) == "Infinity"


def test_decimal_overflow() -> None:
    big = Decimal("1e4000")
    out = _json_default(big)
    assert isinstance(out, str)


def test_decimal128_if_available() -> None:
    if Decimal128 is None:
        return
    d128 = Decimal128("3.14159")
    out = _json_default(d128)
    assert isinstance(out, float) or isinstance(out, str)
