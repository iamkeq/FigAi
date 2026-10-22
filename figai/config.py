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
    # Where spoken-command replies go: your Slack DM, a desktop notification, or both.
    reply_to: Literal["slack", "notification", "both"] = "slack"
    chime: bool = True  # play a "bing" when the wake word is heard, so you know when to talk
    no_speech_timeout_seconds: float = 5.0


class BriefingConfig(BaseModel):
    """Defaults for the daily briefing; `!briefing on|off|time` changes them from chat
    and those changes are remembered across restarts."""

    enabled: bool = False
    time: str = Field(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # local 24h HH:MM
    channel: str | None = None  # Slack channel ID; default is your DM
    include_email: bool = True  # the one part that spends model tokens (the email model, once a day)
    email_limit: int = Field(default=15, ge=1, le=25)


class LimitsConfig(BaseModel):
    max_agent_iterations: int = 10
    max_send_file_mb: int = 25
    confirmation_timeout_seconds: int = 300


class ModelsConfig(BaseModel):
    default_chat: str
    vision: str
    # Optional paid model used only to summarize email. When set, Fig's raw email
    # tools are replaced by summarize_emails, so the main chat model never sees
    # email text, and requests are routed only to providers that don't collect prompts.
    email: str | None = None


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
    # Shared secret for the Chrome tab-bridge extension; the bridge is off without it.
    browser_bridge_token: str | None = None
    # Optional read-only Gmail access (IMAP + app password) for email summaries.
    gmail_address: str | None = None
    gmail_app_password: str | None = None

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
                browser_bridge_token=_bridge_token(),
                **_gmail_credentials(),
            )
        except ValidationError as exc:
            raise ConfigError(f"Invalid .env configuration: {exc}") from exc


def _bridge_token() -> str | None:
    token = os.environ.get("BROWSER_BRIDGE_TOKEN") or None
    if token is not None and len(token) < 16:
        raise ConfigError("BROWSER_BRIDGE_TOKEN must be at least 16 characters (use a long random string)")
    return token


def _gmail_credentials() -> dict[str, str | None]:
    address = os.environ.get("GMAIL_ADDRESS") or None
    password = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "") or None  # Google shows it in groups
    if bool(address) != bool(password):
        raise ConfigError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must both be set, or neither")
    return {"gmail_address": address, "gmail_app_password": password}


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
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Where Fig persists lightweight runtime state (active Slack threads,
    # conversation histories, pending reminders) so it survives a restart.
    state_path: str = "data/figai_state.json"
    # Slack channel (by name) Fig announces "back online" in at startup,
    # including which PR was applied if it just updated itself.
    startup_announcement_channel: str = "general"
    # post_slack_message asks before posting into a channel; false posts straight away.
    confirm_slack_posts: bool = True
    # Localhost port the Chrome tab-bridge extension talks to (see browser_extension/).
    browser_bridge_port: int = 8765

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
