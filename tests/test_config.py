"""Tests for the project-wide environment loader."""

from __future__ import annotations

import os
from importlib import reload

import alpha_engine.config as config


def test_load_project_env_reads_repo_env_files(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "FRED_API_KEY=from-file",
                "BREEZE_API_SECRET='quoted value'",
                "INLINE_VALUE=hello # comment",
                "export ANGEL_ONE_CLIENT_ID=client-123",
            ]
        )
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("BREEZE_API_SECRET", raising=False)
    monkeypatch.delenv("INLINE_VALUE", raising=False)
    monkeypatch.delenv("ANGEL_ONE_CLIENT_ID", raising=False)

    module = reload(config)
    module._ENV_LOADED = False  # noqa: SLF001 - test reset
    module.load_project_env()

    assert os.environ["FRED_API_KEY"] == "from-file"
    assert os.environ["BREEZE_API_SECRET"] == "quoted value"
    assert os.environ["INLINE_VALUE"] == "hello"
    assert os.environ["ANGEL_ONE_CLIENT_ID"] == "client-123"


def test_load_project_env_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("FRED_API_KEY=from-file\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRED_API_KEY", "already-set")

    module = reload(config)
    module._ENV_LOADED = False  # noqa: SLF001 - test reset
    module.load_project_env()

    assert os.environ["FRED_API_KEY"] == "already-set"
