import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

Network = Literal["mainnet", "testnet", "regtest"]
SettlePolicy = Literal["instantsend_or_chainlock", "conf_n"]

YAML_FIELD_MAP: dict[str, str] = {
    "server.bind": "dash_bind",
    "server.port": "dash_port",
    "server.docs_enabled": "dash_docs_enabled",
    "dash.network": "dash_network",
    "mongo.db_name": "mongo_db_name",
    "dashd.rpc_url": "dash_rpc_url",
    "dashd.rpc_wallet": "dash_rpc_wallet",
    "dashd.rpc_user": "dash_rpc_user",
    "wallet.xpub_file": "dash_xpub_file",
    "wallet.mnemonic_file": "dash_mnemonic_file",
    "wallet.descriptor_range_end": "dash_descriptor_range_end",
    "watcher.poll_interval_s": "dash_poll_interval_s",
    "watcher.settle_policy": "dash_settle_policy",
    "watcher.min_conf": "dash_min_conf",
    "watcher.settle_grace_s": "dash_settle_grace_s",
    "watcher.underpay_bps": "dash_underpay_bps",
    "watcher.underpay_duffs": "dash_underpay_duffs",
    "watcher.watch_batch": "dash_watch_batch",
    "watcher.dust_duffs": "dash_dust_duffs",
    "payouts.enabled": "dash_payouts_enabled",
    "quotes.coingecko_url": "coingecko_url",
    "quotes.watch_fallback_url": "watch_fallback_url",
    "quotes.v4v_status_url": "v4v_status_url",
    "quotes.routing_fee_sats": "dash_routing_fee_sats",
}

# Secret leaves: YAML must not carry the value; only the env var *name*.
YAML_ENV_VAR_MAP: dict[str, str] = {
    "dash.api_key_env_var": "dash_api_key",
    "dash.api_key_prev_env_var": "dash_api_key_prev",
    "mongo.uri_env_var": "mongo_uri",
    "dashd.rpc_password_env_var": "dash_rpc_password",
    "wallet.xpub_env_var": "dash_xpub",
    "wallet.master_fingerprint_env_var": "dash_master_fingerprint",
    "quotes.coinmarketcap_api_key_env_var": "coinmarketcap_api_key",
}

FORBIDDEN_YAML_PATHS: frozenset[str] = frozenset(
    {
        "dash.api_key",
        "dash.api_key_prev",
        "mongo.uri",
        "dashd.rpc_password",
        "wallet.xpub",
        "wallet.master_fingerprint",
        "wallet.mnemonic",
        "wallet.xprv",
        "quotes.coinmarketcap_api_key",
        "logging.api_key",
    }
)


class LoggingSettings(BaseModel):
    log_config_file: str = "2-stderr-json-file.json"
    default_log_level: str = "DEBUG"
    console_log_level: str = "INFO"
    log_folder: Path = Path("logs")
    rotation_folder: bool = True
    log_levels: dict[str, str] = Field(default_factory=dict)


def _is_non_mapping_logging(value: object) -> bool:
    if value is None or isinstance(value, dict):
        return False
    if isinstance(value, str):
        return not value.strip().startswith(("{", "["))
    return True


def _env_without_scalar_logging(
    source: PydanticBaseSettingsSource,
) -> PydanticBaseSettingsSource:
    # Leftover LOGGING=debug is not a dash knob; drop it so LOGGING__* still apply.
    original = source.prepare_field_value

    def prepare_field_value(field_name, field, value, value_is_complex):
        if field_name == "logging" and _is_non_mapping_logging(value):
            value = None
        return original(field_name, field, value, value_is_complex)

    source.prepare_field_value = prepare_field_value  # type: ignore[method-assign]
    return source


def _resolve_config_path() -> Path | None:
    raw = os.getenv("DASH_CONFIG", None)
    if raw is None:
        raw = os.getenv("V4VAPP_DASH_CONFIG", None)
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped == "" or stripped.lower() in {"none", "-"}:
        return None
    p = Path(stripped)
    if not p.is_file() and not p.is_absolute():
        p = Path("config") / stripped
    if not p.is_file():
        raise FileNotFoundError(f"DASH_CONFIG={stripped!r} not found at {p}")
    return p


def _flatten(node: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if not isinstance(node, dict):
        return {prefix: node} if prefix else {}
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, dict) and path != "logging":
            out.update(_flatten(val, path))
        else:
            out[path] = val
    return out


def _collect_paths(node: object, prefix: str = "") -> set[str]:
    # Recurse into logging too so logging.api_key cannot hide behind extra=ignore.
    paths: set[str] = set()
    if not isinstance(node, dict):
        if prefix:
            paths.add(prefix)
        return paths
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.add(path)
        if isinstance(val, dict):
            paths.update(_collect_paths(val, path))
    return paths


class DashYamlSettingsSource(PydanticBaseSettingsSource):
    """Custom. Not pydantic_settings.YamlConfigSettingsSource."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        path = _resolve_config_path()
        if path is None:
            return {}
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raw = {}
        # Reject secrets before flatten is applied to Settings (extra=ignore).
        present = _collect_paths(raw)
        for forbidden in FORBIDDEN_YAML_PATHS:
            if forbidden in present:
                raise ValueError(
                    f"secret field {forbidden} is not allowed in YAML; use the matching *_env_var"
                )
        flat = _flatten(raw)
        result: dict[str, Any] = {}
        if "logging" in raw:
            result["logging"] = raw["logging"]
        for dotted, field in YAML_FIELD_MAP.items():
            if dotted in flat:
                result[field] = flat[dotted]
        for dotted, field in YAML_ENV_VAR_MAP.items():
            if dotted not in flat:
                continue
            env_name = flat[dotted]
            if not isinstance(env_name, str) or not env_name:
                continue
            # process env only — names that live solely in .env are not visible
            val = os.getenv(env_name)
            if val is not None:
                result[field] = val
        return result


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

    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _env_without_scalar_logging(env_settings),
            _env_without_scalar_logging(dotenv_settings),
            DashYamlSettingsSource(settings_cls),
            file_secret_settings,
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
