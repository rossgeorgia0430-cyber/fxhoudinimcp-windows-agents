"""Release-level portability checks for the Codex Windows fork.

These tests intentionally do not require Houdini.  They guard the bootstrap
and launcher layer that must work before a clean workstation can connect to a
Houdini session.
"""

from __future__ import annotations

# Built-in
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Third-party
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SCRIPTS = REPO_ROOT / "scripts" / "windows"
REQUIRED_SCRIPTS = (
    "bootstrap.ps1",
    "start-fxhoudinimcp.ps1",
    "start-houdini-fork.ps1",
    "install-houdini-package.ps1",
    "uninstall-houdini-package.ps1",
    "run-full-validation.ps1",
    "verify-hip-node-params.ps1",
)
PORTABLE_TEXT_FILES = (
    REPO_ROOT / ".codex" / "config.toml",
    REPO_ROOT / ".mcp.json",
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "codex-windows.md",
    *(WINDOWS_SCRIPTS / name for name in REQUIRED_SCRIPTS),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _parameter_names(script: str) -> set[str]:
    match = re.search(r"(?ims)^\s*param\s*\((.*?)^\s*\)", script)
    if not match:
        return set()
    return {
        name.lower()
        for name in re.findall(r"\$(\w+)", match.group(1), flags=re.IGNORECASE)
    }


def test_release_scripts_exist() -> None:
    missing = [name for name in REQUIRED_SCRIPTS if not (WINDOWS_SCRIPTS / name).is_file()]
    assert not missing, f"missing Windows release scripts: {missing}"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell AST parser is Windows-specific")
@pytest.mark.parametrize("script_name", REQUIRED_SCRIPTS)
def test_powershell_scripts_parse(script_name: str) -> None:
    script_path = WINDOWS_SCRIPTS / script_name
    escaped_path = str(script_path).replace("'", "''")
    command = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{escaped_path}',"
        "[ref]$tokens,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("path", PORTABLE_TEXT_FILES, ids=lambda path: path.name)
def test_distribution_files_do_not_embed_the_author_machine(path: Path) -> None:
    text = _read(path)
    forbidden_patterns = (
        r"(?i)c:[\\/]+users[\\/]+administrator\b",
        r"(?i)\badmini~1\b",
        r"(?i)houdini\s+21\.0\.440\b",
        r"(?i)d:[\\/]+projects[\\/]+fxhoudinimcp-codex-windows\b",
    )
    found = [
        pattern
        for pattern in forbidden_patterns
        if re.search(pattern, text)
    ]
    assert not found, f"{path.relative_to(REPO_ROOT)} embeds local values: {found}"


def test_codex_config_is_checkout_relative() -> None:
    config = _read(REPO_ROOT / ".codex" / "config.toml")
    assert "[mcp_servers.fxhoudinimcp]" in config
    assert "[mcp_servers.fxhoudini_windows]" not in config
    assert "[projects." not in config
    assert re.search(r'(?m)^\s*cwd\s*=\s*"\."\s*$', config)
    args_match = re.search(r"(?ms)^\s*args\s*=\s*\[(.*?)\]", config)
    assert args_match, "Codex MCP config must declare launcher arguments"
    args = args_match.group(1)
    assert "scripts/windows/start-fxhoudinimcp.ps1" in args.replace("\\", "/")
    assert not re.search(r'(?i)(?:[a-z]:[\\/]|\\\\)', args)


def test_claude_code_project_config_is_checkout_relative() -> None:
    config = json.loads(_read(REPO_ROOT / ".mcp.json"))
    server = config["mcpServers"]["fxhoudinimcp"]
    assert server["type"] == "stdio"
    assert server["command"] == "powershell.exe"
    args = server["args"]
    assert "scripts/windows/start-fxhoudinimcp.ps1" in "/".join(args)
    assert not re.search(r'(?i)(?:[a-z]:[\\/]|\\\\)', " ".join(args))
    assert server["env"] == {
        "HOUDINI_HOST": "127.0.0.1",
        "HOUDINI_PORT": "18100",
        "FXHOUDINIMCP_TOOL_PROFILE": "core",
    }


@pytest.mark.parametrize(
    "script_name",
    (
        "bootstrap.ps1",
        "start-fxhoudinimcp.ps1",
        "start-houdini-fork.ps1",
        "install-houdini-package.ps1",
        "run-full-validation.ps1",
    ),
)
def test_scripts_resolve_the_checkout_from_psscriptroot(script_name: str) -> None:
    script = _read(WINDOWS_SCRIPTS / script_name)
    assert "$PSScriptRoot" in script, f"{script_name} must not depend on the caller's cwd"


def test_bootstrap_contract_is_clean_machine_friendly() -> None:
    script = _read(WINDOWS_SCRIPTS / "bootstrap.ps1")
    parameters = _parameter_names(script)
    assert "python" in parameters
    assert ".venv" in script
    assert re.search(r"(?i)(?:-e|--editable)", script)
    assert re.search(r"(?i)\[dev\]", script)


@pytest.mark.parametrize(
    "script_name",
    ("start-houdini-fork.ps1", "run-full-validation.ps1"),
)
def test_houdini_launchers_autodetect_and_allow_an_override(script_name: str) -> None:
    script = _read(WINDOWS_SCRIPTS / script_name)
    parameters = _parameter_names(script)
    assert "houdiniroot" in parameters
    assert not re.search(
        r"(?i)\$HoudiniRoot\s*=\s*['\"][^'\"]*Houdini\s+\d+\.\d+\.\d+",
        script,
    )
    assert "Side Effects Software" in script
    assert re.search(r"(?i)Get-Command\s+(?:houdini|hython)", script)


@pytest.mark.parametrize(
    "script_name",
    ("install-houdini-package.ps1", "uninstall-houdini-package.ps1"),
)
def test_package_management_supports_redirected_documents(script_name: str) -> None:
    script = _read(WINDOWS_SCRIPTS / script_name)
    parameters = _parameter_names(script)
    assert "documentspath" in parameters
    assert re.search(r"(?i)GetFolderPath\s*\(\s*['\"]MyDocuments['\"]\s*\)", script)
    assert re.search(r"(?i)fxhoudinimcp[-_.]codex[-_.]windows", script)


def test_tool_profile_contract_is_visible_to_codex() -> None:
    config = _read(REPO_ROOT / ".codex" / "config.toml")
    assert re.search(
        r'(?m)^\s*FXHOUDINIMCP_TOOL_PROFILE\s*=\s*"(?:core|modeling|simulation|usd-render|full)"',
        config,
    )


def test_houdini_autostart_module_is_importable() -> None:
    houdini_python = REPO_ROOT / "houdini" / "scripts" / "python"
    sys.path.insert(0, str(houdini_python))
    try:
        import fxhoudinimcp_server.startup as startup
    finally:
        sys.path.remove(str(houdini_python))

    assert callable(startup.ensure_running)


@pytest.mark.skipif(os.name != "nt", reason="package smoke test requires PowerShell")
def test_package_install_and_uninstall_round_trip_in_redirected_documents(
    tmp_path: Path,
) -> None:
    documents = tmp_path / "重定向 Documents with spaces"
    package_dir = documents / "houdini99.9" / "packages"
    package_dir.mkdir(parents=True)
    package_path = package_dir / "fxhoudinimcp-codex-windows.json"
    original = '{"original": true}\n'
    package_path.write_text(original, encoding="utf-8")
    package_preferences = package_dir.parent / "package.pref"
    disabled_autoload = "pkg.autoload := 0;\n"
    package_preferences.write_text(disabled_autoload, encoding="utf-8")

    install = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_SCRIPTS / "install-houdini-package.ps1"),
            "-HoudiniVersion",
            "99.9",
            "-DocumentsPath",
            str(documents),
            "-Port",
            "18123",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    installed = json.loads(package_path.read_text(encoding="utf-8-sig"))
    assert installed["enable"] is True
    assert installed["path"] == "$FXHOUDINIMCP_CODEX_WINDOWS"
    package_env = installed["env"]
    source_entry = next(
        item["FXHOUDINIMCP_CODEX_WINDOWS"]
        for item in package_env
        if "FXHOUDINIMCP_CODEX_WINDOWS" in item
    )
    assert source_entry["value"] == str(REPO_ROOT / "houdini").replace("\\", "/")
    assert source_entry["method"] == "replace"
    assert any(
        item.get("FXHOUDINIMCP_PORT", {}).get("value") == "18123"
        for item in package_env
    )
    assert package_path.with_suffix(".json.backup").is_file()
    assert package_preferences.read_text(encoding="utf-8") == disabled_autoload

    uninstall = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_SCRIPTS / "uninstall-houdini-package.ps1"),
            "-HoudiniVersion",
            "99.9",
            "-DocumentsPath",
            str(documents),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert package_path.read_text(encoding="utf-8") == original
    assert not package_path.with_suffix(".json.backup").exists()
