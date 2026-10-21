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


def test_app_config_voice_defaults_to_disabled(valid_config_yaml: Path) -> None:
    config = AppConfig.from_yaml(valid_config_yaml)

    assert config.voice.enabled is False
    assert config.voice.wake_word_model == "hey_jarvis"


def test_app_config_voice_can_be_enabled_with_custom_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
models:
  default_chat: "x"
  vision: "y"
vault:
  ssh_host_alias: "vault"
  remote_path: "/v"
  local_path: "/tmp/v"
voice:
  enabled: true
  wake_word_model: "alexa"
  wake_word_threshold: 0.7
""",
        encoding="utf-8",
    )

    config = AppConfig.from_yaml(path)

    assert config.voice.enabled is True
    assert config.voice.wake_word_model == "alexa"
    assert config.voice.wake_word_threshold == 0.7


def test_browser_bridge_token_must_be_long_enough(tmp_path: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = write_env(tmp_path)
    monkeypatch.setenv("BROWSER_BRIDGE_TOKEN", "short")
    with pytest.raises(ConfigError):
        Settings.from_env(env_path)


def test_browser_bridge_token_is_read_when_valid(tmp_path: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_BRIDGE_TOKEN", "x" * 24)
    assert Settings.from_env(write_env(tmp_path)).browser_bridge_token == "x" * 24


def test_gmail_credentials_require_both_or_neither(tmp_path: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    with pytest.raises(ConfigError):
        Settings.from_env(write_env(tmp_path))


def test_gmail_app_password_spaces_are_stripped(tmp_path: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    assert Settings.from_env(write_env(tmp_path)).gmail_app_password == "abcdefghijklmnop"
