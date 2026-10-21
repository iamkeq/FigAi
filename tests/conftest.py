from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OPENROUTER_", "SLACK_", "ALLOWED_SLACK_", "SSH_HOST_", "OBS_WS_")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def valid_config_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
models:
  default_chat: "openai/gpt-5.6-luna"
  vision: "google/gemini-3.1-flash-lite-image"
vault:
  ssh_host_alias: "vault"
  remote_path: "/home/matt/ObsidianVault"
  local_path: "/tmp/local_vault"
allowlisted_programs:
  steam: "C:\\\\Steam\\\\Steam.exe"
allowlisted_folders:
  - "C:\\\\Users\\\\matt\\\\Documents"
""",
        encoding="utf-8",
    )
    return path
