"""Loads and validates Fig's configuration.

Secrets (API keys, tokens, SSH connection details) live in `.env`.
Everything else (models, allowlists, limits, vault paths) lives in `config.yaml`.
Neither file is ever logged or printed in full.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

DEFAULT_CONFIG_PATH = Path("config/config.yaml")
DEFAULT_ENV_PATH = Path(".env")


class SshHost(BaseModel):
    host: str
    user: str
    port: int = 22
    key_path: str


class ServiceConnection(BaseModel):
    url: str
    api_key: str

    @classmethod
    def from_env_pair(cls, url_var: str, api_key_var: str) -> "ServiceConnection | None":
        url = os.environ.get(url_var)
        api_key = os.environ.get(api_key_var)
        if not url and not api_key:
            return None
        if not url or not api_key:
            raise ConfigError(f"{url_var} and {api_key_var} must both be set, or neither")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ConfigError(f"{url_var} must be an http(s) URL")
        return cls(url=url, api_key=api_key)


class ObsConnection(BaseModel):
    host: str
    port: int = 4455
    password: str | None = None

    @classmethod
    def from_env(cls) -> "ObsConnection | None":
        host = os.environ.get("OBS_WS_HOST")
        if not host:
            return None
        port_raw = os.environ.get("OBS_WS_PORT", "4455")
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigError(f"OBS_WS_PORT must be a number, got: {port_raw!r}") from None
        return cls(host=host, port=port, password=os.environ.get("OBS_WS_PASSWORD") or None)


class VaultConfig(BaseModel):
    ssh_host_alias: str
    remote_path: str
    remote_writable_subfolder: str = "AI"
    local_path: str
    index_note: str = "AI/index.md"


class VoiceConfig(BaseModel):
    enabled: bool = False
    # openWakeWord ships pretrained models for specific words ("hey_jarvis",
    # "alexa", "hey_mycroft", ...); there's no built-in "fig" model. Train a
    # custom one with openWakeWord's training notebook and point this at its
    # file path once you have it -- until then this is a working stand-in.
    wake_word_model: str = "hey_jarvis"
    wake_word_threshold: float = 0.5
    whisper_model: str = "base.en"
    silence_seconds: float = 1.2
    max_command_seconds: float = 15.0


class LimitsConfig(BaseModel):
    max_agent_iterations: int = 10
    max_send_file_mb: int = 25
    confirmation_timeout_seconds: int = 300


class ModelsConfig(BaseModel):
    default_chat: str
    vision: str


class Settings(BaseModel):
    """Values loaded from `.env` (secrets)."""

    openrouter_api_key: str
    slack_bot_token: str
    slack_app_token: str
    allowed_slack_user_id: str
    ssh_hosts: dict[str, SshHost] = Field(default_factory=dict)
    sonarr: ServiceConnection | None = None
    radarr: ServiceConnection | None = None
    sabnzbd: ServiceConnection | None = None
    obs: ObsConnection | None = None
    # Optional: lets propose_update_fig look up which PR/labels a commit came
    # from on a private repo. Not needed for a public repo; omit if unused.
    github_token: str | None = None

    @classmethod
    def from_env(cls, env_path: Path = DEFAULT_ENV_PATH) -> "Settings":
        if env_path.exists():
            load_dotenv(env_path, override=True)

        ssh_hosts: dict[str, SshHost] = {}
        for key, value in os.environ.items():
            if not key.startswith("SSH_HOST_"):
                continue
            alias = key.removeprefix("SSH_HOST_").lower()
            parts = dict(item.split("=", 1) for item in value.split(";") if "=" in item)
            ssh_hosts[alias] = SshHost(
                host=parts["host"],
                user=parts["user"],
                port=int(parts.get("port", 22)),
                key_path=parts["key_path"],
            )

        try:
            return cls(
                openrouter_api_key=_require_env("OPENROUTER_API_KEY"),
                slack_bot_token=_require_env("SLACK_BOT_TOKEN"),
                slack_app_token=_require_env("SLACK_APP_TOKEN"),
                allowed_slack_user_id=_require_env("ALLOWED_SLACK_USER_ID"),
                ssh_hosts=ssh_hosts,
                sonarr=ServiceConnection.from_env_pair("SONARR_URL", "SONARR_API_KEY"),
                radarr=ServiceConnection.from_env_pair("RADARR_URL", "RADARR_API_KEY"),
                sabnzbd=ServiceConnection.from_env_pair("SABNZBD_URL", "SABNZBD_API_KEY"),
                obs=ObsConnection.from_env(),
                github_token=os.environ.get("GITHUB_TOKEN") or None,
            )
        except ValidationError as exc:
            raise ConfigError(f"Invalid .env configuration: {exc}") from exc


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing required .env value: {name}")
    return value


class AppConfig(BaseModel):
    """Values loaded from `config.yaml` (non-secret settings)."""

    models: ModelsConfig
    vault: VaultConfig
    allowlisted_folders: list[str] = Field(default_factory=list)
    allowlisted_programs: dict[str, str] = Field(default_factory=dict)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Where Fig persists lightweight runtime state (active Slack threads,
    # conversation histories, pending reminders) so it survives a restart.
    state_path: str = "data/figai_state.json"
    # Slack channel (by name) Fig announces "back online" in at startup,
    # including which PR was applied if it just updated itself.
    startup_announcement_channel: str = "general"

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        if not path.exists():
            raise ConfigError(
                f"Missing config file: {path}. Copy config/config.example.yaml and edit it."
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return cls(**raw)
        except ValidationError as exc:
            raise ConfigError(f"Invalid {path}: {exc}") from exc


class ConfigError(Exception):
    """Raised when configuration is missing or invalid. Fails closed."""


class Config(BaseModel):
    """The full, merged configuration Fig runs with."""

    settings: Settings
    app: AppConfig

    @classmethod
    def load(
        cls,
        env_path: Path = DEFAULT_ENV_PATH,
        config_path: Path = DEFAULT_CONFIG_PATH,
    ) -> "Config":
        return cls(settings=Settings.from_env(env_path), app=AppConfig.from_yaml(config_path))
