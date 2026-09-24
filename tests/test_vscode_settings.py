import json
from pathlib import Path

import pytest

from tutorial_ai.automation import vscode as vscode_module
from tutorial_ai.automation.vscode import TUTORIAL_WORKSPACE_SETTINGS, VSCodeController


def test_settings_disable_features_that_corrupt_typed_code():
    s = TUTORIAL_WORKSPACE_SETTINGS
    assert s["editor.autoClosingBrackets"] == "never"
    assert s["editor.autoClosingQuotes"] == "never"
    assert s["editor.autoIndent"] == "none"
    assert s["editor.acceptSuggestionOnEnter"] == "off"
    assert s["editor.formatOnType"] is False


def test_prepare_workspace_writes_settings_and_paths_stay_inside(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vscode_module, "find_code_cli", lambda: "code")
    controller = VSCodeController(tmp_path)
    controller.prepare_workspace()
    written = json.loads((tmp_path / ".vscode" / "settings.json").read_text())
    assert written == TUTORIAL_WORKSPACE_SETTINGS
    assert controller.path("app/main.py") == tmp_path.resolve() / "app/main.py"
    with pytest.raises(ValueError):
        controller.path("../outside.py")


def test_profile_settings_isolate_the_tutorial_vscode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vscode_module, "find_code_cli", lambda: "code")
    controller = VSCodeController(tmp_path / "ws", profile_root=tmp_path / "profile")
    controller.prepare_profile()
    settings = json.loads((tmp_path / "profile" / "user-data" / "User" / "settings.json").read_text())
    assert settings["window.restoreWindows"] == "none"
    assert settings["editor.autoClosingBrackets"] == "never"  # editor settings apply profile-wide too
    assert (tmp_path / "profile" / "extensions").is_dir()
