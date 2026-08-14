from pathlib import Path

import pytest
import yaml

from v4vapp_dash.config import (
    FORBIDDEN_YAML_PATHS,
    YAML_ENV_VAR_MAP,
    YAML_FIELD_MAP,
    DashYamlSettingsSource,
    Settings,
    get_settings,
)

SAMPLE = Path("tests/data/config/sample.config.yaml")


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "test.config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _nest(dotted: str, value: object) -> dict:
    keys = dotted.split(".")
    root: dict = {}
    cur = root
    for key in keys[:-1]:
        cur = cur.setdefault(key, {})
    cur[keys[-1]] = value
    return root


def test_load_sample_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_CONFIG", str(SAMPLE))
    # _env_file=None: developer .env must not override fixture values
    settings = Settings(_env_file=None)
    assert settings.dash_bind == "0.0.0.0"
    assert settings.dash_port == 8080
    assert settings.dash_network == "regtest"
    assert settings.mongo_db_name == "v4vapp-dev"
    assert settings.dash_rpc_url == "http://dashd:9998"
    assert settings.dash_xpub_file == ""
    assert settings.logging.log_config_file == "2-stderr-json-file.json"
    assert settings.logging.default_log_level == "DEBUG"
    assert settings.logging.log_levels["uvicorn"] == "WARNING"
    assert settings.logging.log_levels["uvicorn.access"] == "WARNING"


def test_env_wins_over_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_CONFIG", str(SAMPLE))
    monkeypatch.setenv("DASH_PORT", "9999")
    get_settings.cache_clear()
    assert get_settings().dash_port == 9999


def test_yaml_sets_distinct_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_yaml(
        tmp_path,
        {
            "server": {"port": 12345, "bind": "127.0.0.1"},
            "watcher": {"poll_interval_s": 42},
            "logging": {"console_log_level": "ERROR"},
        },
    )
    monkeypatch.delenv("DASH_PORT", raising=False)
    monkeypatch.delenv("DASH_BIND", raising=False)
    monkeypatch.delenv("DASH_POLL_INTERVAL_S", raising=False)
    monkeypatch.setenv("DASH_CONFIG", str(path))
    settings = Settings(_env_file=None)
    assert settings.dash_port == 12345
    assert settings.dash_bind == "127.0.0.1"
    assert settings.dash_poll_interval_s == 42
    assert settings.logging.console_log_level == "ERROR"


def test_nested_logging_env_wins_over_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_yaml(tmp_path, {"logging": {"console_log_level": "DEBUG"}})
    monkeypatch.setenv("DASH_CONFIG", str(path))
    monkeypatch.setenv("LOGGING__CONSOLE_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    assert get_settings().logging.console_log_level == "WARNING"


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_YAML_PATHS))
def test_forbidden_yaml_path_raises(
    forbidden: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_yaml(tmp_path, _nest(forbidden, ""))
    monkeypatch.setenv("DASH_CONFIG", str(path))
    with pytest.raises(ValueError, match=forbidden):
        Settings(_env_file=None)


def test_allowed_env_var_and_file_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_yaml(
        tmp_path,
        {
            "dash": {"api_key_env_var": "DASH_API_KEY"},
            "wallet": {"xpub_file": "", "xpub_env_var": "DASH_XPUB"},
        },
    )
    monkeypatch.setenv("DASH_CONFIG", str(path))
    Settings(_env_file=None)


def test_env_var_resolves_from_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_yaml(tmp_path, {"dash": {"api_key_env_var": "CUSTOM_DASH_KEY"}})
    monkeypatch.setenv("CUSTOM_DASH_KEY", "from-process-env")
    monkeypatch.setenv("DASH_CONFIG", str(path))
    result = DashYamlSettingsSource(Settings)()
    assert result["dash_api_key"] == "from-process-env"


@pytest.mark.parametrize("value", ["", "none", "NONE", "-", "  none  "])
def test_dash_config_empty_or_none_skips_yaml(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = _write_yaml(tmp_path, {"server": {"port": 1111}})
    monkeypatch.setenv("DASH_CONFIG", value)
    # leftover file must not be auto-loaded
    assert leftover.is_file()
    assert Settings(_env_file=None).dash_port == 8080


def test_dash_config_unset_skips_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASH_CONFIG", raising=False)
    monkeypatch.delenv("V4VAPP_DASH_CONFIG", raising=False)
    settings = Settings(_env_file=None)
    assert settings.dash_port == 8080
    assert settings.logging.log_levels == {}


def test_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASH_CONFIG", "does-not-exist.yaml")
    with pytest.raises(FileNotFoundError, match="does-not-exist.yaml"):
        Settings(_env_file=None)


def test_v4vapp_dash_config_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASH_CONFIG", raising=False)
    monkeypatch.setenv("V4VAPP_DASH_CONFIG", str(SAMPLE))
    assert Settings(_env_file=None).logging.log_levels["uvicorn"] == "WARNING"


def test_get_field_value_stub() -> None:
    source = DashYamlSettingsSource(Settings)
    field = Settings.model_fields["dash_port"]
    assert source.get_field_value(field, "dash_port") == (None, "", False)


def test_every_settings_field_is_mapped() -> None:
    mapped = set(YAML_FIELD_MAP.values()) | set(YAML_ENV_VAR_MAP.values()) | {"logging"}
    assert set(Settings.model_fields) == mapped


def test_sample_yaml_has_no_forbidden_paths() -> None:
    raw = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    text = SAMPLE.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_YAML_PATHS:
        keys = forbidden.split(".")
        node = raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            raise AssertionError(f"forbidden path {forbidden} present in sample")
    assert "xpub_file:" in text
    assert "xpub_env_var:" in text
