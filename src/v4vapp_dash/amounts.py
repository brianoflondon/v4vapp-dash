from decimal import Decimal
from typing import Any

DUFFS_PER_DASH = Decimal("100000000")
SATS_PER_BTC = 100_000_000


def rpc_dash_to_duffs(amount: Any) -> int:
    """dashd returns DASH as JSON floats — never use float * 1e8."""
    return int((Decimal(str(amount)) * DUFFS_PER_DASH).to_integral_value())


def dash_amount_string(duffs: int) -> str:
    return f"{(Decimal(duffs) / DUFFS_PER_DASH):.8f}"
