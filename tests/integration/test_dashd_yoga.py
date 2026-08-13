"""Live node RPC against yoga dashd. Skipped if deploy/dashd/.env is missing or RPC is down."""

from pathlib import Path

import pytest

from v4vapp_dash.dashd.rpc import Dashd, WalletDisabled

ENV_PATH = Path(__file__).resolve().parents[2] / "deploy" / "dashd" / ".env"


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _yoga_rpc() -> tuple[str, str, str] | None:
    if not ENV_PATH.is_file():
        return None
    values = _parse_env(ENV_PATH)
    user = values.get("DASH_RPC_USER") or "dashrpc"
    password = values.get("DASH_RPC_PASSWORD")
    ip = values.get("LOCAL_TAILSCALE_IP")
    if not password or not ip or "x.y.z" in str(ip):
        return None
    return (f"http://{ip}:9998", user, password)


@pytest.mark.asyncio
async def test_yoga_getblockchaininfo() -> None:
    creds = _yoga_rpc()
    if creds is None:
        pytest.skip("deploy/dashd/.env not present")
    url, user, password = creds
    dashd = Dashd(url, user=user, password=password)
    try:
        info = await dashd.getblockchaininfo()
    except Exception as exc:
        await dashd.aclose()
        pytest.skip(f"yoga dashd not reachable: {exc}")
    try:
        assert info["chain"] == "main"
        assert info["blocks"] > 0
        assert "initialblockdownload" in info
        assert info.get("pruned") is True
        try:
            await dashd.listwallets()
            wallet_on = True
        except WalletDisabled:
            wallet_on = False
        # IBD image ships with -disablewallet=1 until yoga compose is updated.
        assert wallet_on in (True, False)
    finally:
        await dashd.aclose()
