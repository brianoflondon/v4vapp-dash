from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Network = Literal["mainnet", "testnet", "regtest"]
SettlePolicy = Literal["instantsend_or_chainlock", "conf_n"]


class LoggingSettings(BaseModel):
    log_config_file: str = "2-stderr-json-file.json"
    default_log_level: str = "DEBUG"
    console_log_level: str = "INFO"
    log_folder: Path = Path("logs")
    rotation_folder: bool = True
    log_levels: dict[str, str] = Field(default_factory=dict)


def _coerce_logging(value: object) -> LoggingSettings:
    # LOGGING=debug is not a dash knob (use LOGGING__CONSOLE_LOG_LEVEL).
    if isinstance(value, LoggingSettings):
        return value
    if isinstance(value, dict):
        return LoggingSettings.model_validate(value)
    return LoggingSettings()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_nested_delimiter="__",
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
    dash_dust_duffs: int = 5460
    dash_payouts_enabled: bool = False

    coingecko_url: str = "https://api.coingecko.com/api/v3/simple/price"
    coinmarketcap_api_key: str = ""
    watch_fallback_url: str = ""
    v4v_status_url: str = "https://api.v4v.app/v1"
    dash_routing_fee_sats: int = 300

    logging: Annotated[LoggingSettings, NoDecode, BeforeValidator(_coerce_logging)] = Field(
        default_factory=LoggingSettings
    )


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
