from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from figai.agent.tools import ToolRegistry
from figai.tools.config_edit import register_config_edit_tools
from figai.tools.confirm import ConfirmationStore

CONFIG = """# my config
models:
  default_chat: "a/b"   # main
  vision: "c/d"

vault:
  ssh_host_alias: "vault"
  remote_path: "/x"
  local_path: "/y"

allowlisted_folders:
  - "/docs"
"""
ENV = "OPENROUTER_API_KEY=sk-secret\nSLACK_BOT_TOKEN=xoxb-secret\nGMAIL_ADDRESS=old@x.com\n"


@pytest.fixture
def setup(tmp_path: Path):
    config_path, env_path = tmp_path / "config.yaml", tmp_path / ".env"
    config_path.write_text(CONFIG, encoding="utf-8")
    env_path.write_text(ENV, encoding="utf-8")
    registry = ToolRegistry()
    register_config_edit_tools(registry, ConfirmationStore(), config_path, env_path)

    def call(name: str, **args):
        return json.loads(registry.execute(name, json.dumps(args)))

    def change(**args):
        proposed = call("propose_config_change", **args)
        assert proposed["status"] == "awaiting_confirmation", proposed
        return call("resolve_config_change", token=proposed["token"])

    return call, change, config_path, env_path


def test_get_config_hides_secrets(setup) -> None:
    call, _, _, _ = setup
    result = call("get_config")
    text = json.dumps(result)
    assert "sk-secret" not in text and "xoxb-secret" not in text
    assert result["env_editable"] == {"GMAIL_ADDRESS": "old@x.com"}
    assert set(result["env_secret_keys_set_but_hidden"]) == {"OPENROUTER_API_KEY", "SLACK_BOT_TOKEN"}


def test_yaml_scalar_edit_keeps_comments_and_makes_backup(setup) -> None:
    _, change, config_path, _ = setup
    result = change(file="config", key="vault.ssh_host_alias", value="homeserver")
    assert result["status"] == "saved"
    text = config_path.read_text(encoding="utf-8")
    assert '# my config' in text and "# main" in text
    assert yaml.safe_load(text)["vault"]["ssh_host_alias"] == "homeserver"
    assert config_path.with_name("config.yaml.bak").read_text(encoding="utf-8") == CONFIG


def test_yaml_new_key_falls_back_to_rewrite(setup) -> None:
    _, change, config_path, _ = setup
    result = change(file="config", key="models.email", value="anthropic/claude-haiku-5.5")
    assert "comments were dropped" in result["note"]
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["models"]["email"] == "anthropic/claude-haiku-5.5"


def test_nothing_changes_until_resolved(setup) -> None:
    call, _, config_path, _ = setup
    call("propose_config_change", file="config", key="vault.ssh_host_alias", value="x")
    assert config_path.read_text(encoding="utf-8") == CONFIG


@pytest.mark.parametrize("key", ["allowlisted_folders", "limits.max_agent_iterations", "state_path"])
def test_protected_yaml_sections_refused(setup, key) -> None:
    call, _, _, _ = setup
    assert "error" in call("propose_config_change", file="config", key=key, value="1")


def test_invalid_yaml_change_refused(setup) -> None:
    call, _, config_path, _ = setup
    assert "error" in call("propose_config_change", file="config", key="log_level", value="LOUD")
    assert config_path.read_text(encoding="utf-8") == CONFIG


@pytest.mark.parametrize("key", ["OPENROUTER_API_KEY", "SLACK_BOT_TOKEN", "GMAIL_APP_PASSWORD", "ALLOWED_SLACK_USER_ID", "PATH"])
def test_secret_env_keys_refused(setup, key) -> None:
    call, _, _, env_path = setup
    assert "error" in call("propose_config_change", file="env", key=key, value="oops")
    assert env_path.read_text(encoding="utf-8") == ENV


def test_env_edit_replaces_and_appends(setup) -> None:
    _, change, _, env_path = setup
    change(file="env", key="GMAIL_ADDRESS", value="new@x.com")
    change(file="env", key="SSH_HOST_HOMESERVER", value="host=192.168.50.51;user=dave;key_path=C:\\k\\id")
    text = env_path.read_text(encoding="utf-8")
    assert "GMAIL_ADDRESS=new@x.com" in text and "old@x.com" not in text
    assert "SSH_HOST_HOMESERVER=host=192.168.50.51;user=dave;key_path=C:\\k\\id" in text
    assert "OPENROUTER_API_KEY=sk-secret" in text
    # the backup is the file as it was before the most recent change
    assert "SSH_HOST_HOMESERVER" not in env_path.with_name(".env.bak").read_text(encoding="utf-8")


def test_env_validation(setup) -> None:
    call, _, _, _ = setup
    assert "error" in call("propose_config_change", file="env", key="SSH_HOST_X", value="host=1.2.3.4")
    assert "error" in call("propose_config_change", file="env", key="SONARR_URL", value="not-a-url")
    assert "error" in call("propose_config_change", file="env", key="GMAIL_ADDRESS", value="a\nB=c")


def test_bad_token_rejected(setup) -> None:
    call, _, _, _ = setup
    assert "error" in call("resolve_config_change", token="nope")
