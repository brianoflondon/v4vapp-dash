from v4vapp_dash.config import Network
from v4vapp_dash.wallet.hd import COIN_TYPE


def origin_path(network: Network) -> str:
    """BIP32 origin after the master fingerprint: 44h/<coin>h/0h."""
    return f"44h/{COIN_TYPE[network]}h/0h"


def raw_descriptor(
    fingerprint: str,
    account_xpub: str,
    network: Network,
    *,
    change: bool,
) -> str:
    """Unchecksummed BIP380 pkh descriptor with origin. No `watchonly` field anywhere."""
    fp = fingerprint.lower()
    if len(fp) != 8:
        raise ValueError("master fingerprint must be 8 hex characters")
    branch = 1 if change else 0
    return f"pkh([{fp}/{origin_path(network)}]{account_xpub}/{branch}/*)"


def checksummed_descriptor(getdescriptorinfo_result: dict[str, object], raw: str) -> str:
    desc = getdescriptorinfo_result.get("descriptor")
    if isinstance(desc, str) and "#" in desc:
        return desc
    checksum = getdescriptorinfo_result.get("checksum")
    if isinstance(checksum, str) and checksum:
        return f"{raw}#{checksum}"
    raise ValueError("getdescriptorinfo did not return a checksum")


def import_request(desc: str, *, internal: bool, range_end: int) -> dict[str, object]:
    return {
        "desc": desc,
        "timestamp": "now",
        "active": True,
        "internal": internal,
        "range": [0, range_end],
    }
