from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Network = Literal["mainnet", "testnet", "regtest"]
SettlePolicy = Literal["instantsend_or_chainlock", "conf_n"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    dash_network: Network = "regtest"
    dash_api_key: str = ""
    dash_api_key_prev: str = ""
    dash_bind: str = "0.0.0.0"
    dash_port: int = 8080
    dash_docs_enabled: bool = False

    mongo_uri: str = ""
    mongo_db_name: str = "v4vapp-dev"

    dash_rpc_url: str = "http://dashd:9998"
    dash_rpc_wallet: str = "watch"
    dash_rpc_user: str = "dashrpc"
    dash_rpc_password: str = ""

    dash_xpub_file: str = ""
    dash_xpub: str = ""
    dash_master_fingerprint: str = ""
    dash_mnemonic_file: str = ""

    dash_poll_interval_s: int = 10
    dash_settle_policy: SettlePolicy = "conf_n"
    dash_min_conf: int = 1
    dash_settle_grace_s: int = 3600
    dash_underpay_bps: int = 100
    dash_underpay_duffs: int = 50_000
    dash_descriptor_range_end: int = 100_000
    dash_watch_batch: int = 500
    dash_payouts_enabled: bool = False

    coingecko_url: str = "https://api.coingecko.com/api/v3/simple/price"
    coinmarketcap_api_key: str = ""
    watch_fallback_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def default_min_conf(network: Network) -> int:
    if network == "regtest":
        return 1
    if network == "testnet":
        return 2
    return 6


def default_settle_policy(network: Network) -> SettlePolicy:
    if network == "regtest":
        return "conf_n"
    return "instantsend_or_chainlock"
