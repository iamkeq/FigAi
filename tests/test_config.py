from __future__ import annotations

from pathlib import Path

import pytest

from figai.config import AppConfig, ConfigError, ObsConnection, Settings


def write_env(path: Path, **overrides: str) -> Path:
    values = {
        "OPENROUTER_API_KEY": "test-key",
        "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_APP_TOKEN": "xapp-test",
        "ALLOWED_SLACK_USER_ID": "U0000000",
        "SSH_HOST_VAULT": "host=10.0.0.5;user=test;port=22;key_path=/tmp/fake_key",
        **overrides,
    }
    env_path = path / ".env"
    env_path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return env_path


def test_settings_from_env_loads_ssh_hosts(tmp_path: Path, clean_env: None) -> None:
    env_path = write_env(tmp_path)

    settings = Settings.from_env(env_path)

    assert settings.openrouter_api_key == "test-key"
    assert settings.allowed_slack_user_id == "U0000000"
    assert "vault" in settings.ssh_hosts
    host = settings.ssh_hosts["vault"]
    assert host.host == "10.0.0.5"
    assert host.user == "test"
    assert host.port == 22
    assert host.key_path == "/tmp/fake_key"


def test_settings_missing_required_value_fails_closed(tmp_path: Path, clean_env: None) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=only-this\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Settings.from_env(env_path)


def test_app_config_from_yaml(valid_config_yaml: Path) -> None:
    config = AppConfig.from_yaml(valid_config_yaml)

    assert config.models.default_chat == "openai/gpt-5.6-luna"
    assert config.vault.ssh_host_alias == "vault"
    assert config.vault.remote_writable_subfolder == "AI"
    assert config.limits.max_agent_iterations == 10
    assert "steam" in config.allowlisted_programs


def test_app_config_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        AppConfig.from_yaml(tmp_path / "nope.yaml")


def test_obs_connection_absent_when_host_unset(clean_env: None) -> None:
    assert ObsConnection.from_env() is None


def test_obs_connection_parses_host_port_password(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("OBS_WS_HOST", "127.0.0.1")
    monkeypatch.setenv("OBS_WS_PORT", "4456")
    monkeypatch.setenv("OBS_WS_PASSWORD", "secret")

    conn = ObsConnection.from_env()

    assert conn == ObsConnection(host="127.0.0.1", port=4456, password="secret")


def test_obs_connection_defaults_port_and_allows_no_password(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("OBS_WS_HOST", "127.0.0.1")

    conn = ObsConnection.from_env()

    assert conn == ObsConnection(host="127.0.0.1", port=4455, password=None)


def test_obs_connection_invalid_port_fails_closed(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("OBS_WS_HOST", "127.0.0.1")
    monkeypatch.setenv("OBS_WS_PORT", "not-a-number")

    with pytest.raises(ConfigError):
        ObsConnection.from_env()
