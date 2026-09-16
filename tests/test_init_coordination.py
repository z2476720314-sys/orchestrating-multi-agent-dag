from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
INIT_SCRIPT = SKILL_ROOT / "scripts" / "init-coordination.ps1"
VALIDATE_SCRIPT = SKILL_ROOT / "scripts" / "validate-coordination.ps1"


def run_powershell(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *arguments,
    ]
    return subprocess.run(command, check=False, capture_output=True, text=True)


class CoordinationInitializerTests(unittest.TestCase):
    def test_initializes_complete_coordination_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            result = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            coordination = workspace / ".agent-coordination"
            expected = (
                coordination / "README.md",
                coordination / "status.md",
                coordination / "handoff-contract.md",
                coordination / "observer" / "run.py",
                coordination / "observer" / "static" / "app.js",
                coordination / "observer" / "observer" / "engine.py",
            )
            for path in expected:
                self.assertTrue(path.is_file(), f"missing {path}")
            self.assertIn("created:", result.stdout)

    def test_repeat_run_preserves_existing_coordination_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            first = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(first.returncode, 0, first.stderr or first.stdout)

            status_path = workspace / ".agent-coordination" / "status.md"
            status_path.write_text("USER STATUS\n", encoding="utf-8")
            observer_file = workspace / ".agent-coordination" / "observer" / "VERSION"
            observer_file.write_text("USER OBSERVER\n", encoding="utf-8")

            second = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
            self.assertEqual(status_path.read_text(encoding="utf-8"), "USER STATUS\n")
            self.assertEqual(observer_file.read_text(encoding="utf-8"), "USER OBSERVER\n")
            self.assertIn("skipped:", second.stdout)

    def test_copy_filters_runtime_residue_even_if_template_contains_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            fake_skill = temporary_root / "fake-skill"
            fake_script = fake_skill / "scripts" / INIT_SCRIPT.name
            fake_template = fake_skill / "assets" / "observer-template"
            workspace = temporary_root / "workspace"
            fake_script.parent.mkdir(parents=True)
            workspace.mkdir()
            shutil.copy2(INIT_SCRIPT, fake_script)

            allowed_files = (
                fake_template / "run.py",
                fake_template / "static" / "app.js",
            )
            forbidden_files = (
                fake_template / "__pycache__" / "run.cpython-313.pyc",
                fake_template / "observer" / "__pycache__" / "engine.pyc",
                fake_template / ".mypy_cache" / "cache.db",
                fake_template / ".ruff_cache" / "cache",
                fake_template / ".pytest_cache" / "cache",
                fake_template / "artifacts" / "snapshot.png",
                fake_template / "runtime.log",
                fake_template / "VERSION.bak-20260912",
                fake_template / ".coverage",
            )
            for path in (*allowed_files, *forbidden_files):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

            result = run_powershell(fake_script, "-Workspace", str(workspace))

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            observer = workspace / ".agent-coordination" / "observer"
            for path in allowed_files:
                relative = path.relative_to(fake_template)
                self.assertTrue((observer / relative).is_file(), str(relative))
            for path in forbidden_files:
                relative = path.relative_to(fake_template)
                self.assertFalse((observer / relative).exists(), str(relative))

    def test_explicit_observer_refresh_backs_up_code_only_and_replaces_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            first = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(first.returncode, 0, first.stderr or first.stdout)

            target_version = workspace / ".agent-coordination" / "observer" / "VERSION"
            target_version.write_text("USER OBSERVER\n", encoding="utf-8")
            target_code = workspace / ".agent-coordination" / "observer" / "static" / "app.js"
            target_code.write_text("USER CODE\n", encoding="utf-8")
            expected_version = (SKILL_ROOT / "assets" / "observer-template" / "VERSION").read_text(
                encoding="utf-8"
            )

            refreshed = run_powershell(
                INIT_SCRIPT,
                "-Workspace",
                str(workspace),
                "-RefreshObserver",
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr or refreshed.stdout)
            self.assertEqual(target_version.read_text(encoding="utf-8"), expected_version)
            self.assertEqual(tuple(target_version.parent.glob("VERSION.bak-*")), ())
            code_backups = tuple(target_code.parent.glob("app.js.bak-*"))
            self.assertEqual(len(code_backups), 1)
            self.assertEqual(code_backups[0].read_text(encoding="utf-8"), "USER CODE\n")
            self.assertIn("refreshed: 2", refreshed.stdout)

            target_version.write_text("SECOND USER OBSERVER\n", encoding="utf-8")
            refreshed_again = run_powershell(
                INIT_SCRIPT,
                "-Workspace",
                str(workspace),
                "-RefreshObserver",
            )
            self.assertEqual(
                refreshed_again.returncode,
                0,
                refreshed_again.stderr or refreshed_again.stdout,
            )
            self.assertEqual(tuple(target_version.parent.glob("VERSION.bak-*")), ())

    def test_rejects_filesystem_root_as_workspace(self) -> None:
        filesystem_root = Path.cwd().anchor
        result = run_powershell(INIT_SCRIPT, "-Workspace", filesystem_root)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to initialize a filesystem root", result.stderr)

    def test_template_excludes_runtime_artifacts_backups_and_credentials(self) -> None:
        template = SKILL_ROOT / "assets" / "observer-template"
        forbidden_parts = {"artifacts", "__pycache__", ".mypy_cache", ".ruff_cache"}
        forbidden_names = {"settings.yaml", ".credentials.yaml", "cookies.json"}

        for path in template.rglob("*"):
            self.assertFalse(forbidden_parts.intersection(path.parts), str(path))
            self.assertNotIn(path.name, forbidden_names)
            self.assertNotIn(".bak", path.name.lower())
            self.assertNotEqual(path.suffix.lower(), ".pyc")

    def test_skill_contains_no_machine_specific_windows_user_path(self) -> None:
        windows_user_path = re.compile(r"[A-Za-z]:\\Users\\[^\\]+\\")
        text_suffixes = {".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".yaml"}

        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(windows_user_path.search(content), str(path))

    def test_rejects_reparse_point_coordination_directory(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_workspace,
            tempfile.TemporaryDirectory() as external_directory,
        ):
            workspace = Path(temporary_workspace)
            coordination = workspace / ".agent-coordination"
            junction_command = (
                "New-Item -ItemType Junction "
                f"-Path '{coordination}' -Target '{external_directory}' | Out-Null"
            )
            junction = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    junction_command,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(junction.returncode, 0, junction.stderr or junction.stdout)

            result = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reparse point", result.stderr.lower())
            self.assertEqual(tuple(Path(external_directory).iterdir()), ())

            validation = run_powershell(
                VALIDATE_SCRIPT, "-Workspace", str(workspace)
            )
            self.assertNotEqual(validation.returncode, 0)
            self.assertIn("reparse point", validation.stderr.lower())

    def test_generated_observer_emits_a_versioned_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            initialized = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(
                initialized.returncode, 0, initialized.stderr or initialized.stdout
            )

            observer = workspace / ".agent-coordination" / "observer"
            snapshot = subprocess.run(
                [sys.executable, str(observer / "run.py"), "--workspace", str(workspace), "--once"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(snapshot.returncode, 0, snapshot.stderr or snapshot.stdout)
            payload = json.loads(snapshot.stdout)
            self.assertEqual(payload["schema_version"], "1")
            self.assertEqual(Path(payload["workspace"]).resolve(), workspace.resolve())
            coordination_agent_ids = {
                agent["id"]
                for agent in payload["agents"]
                if agent["id"].startswith("coordination:")
            }
            self.assertEqual(
                coordination_agent_ids,
                {
                    "coordination:codex",
                    "coordination:dsh",
                    "coordination:workbuddy",
                },
            )
            self.assertNotIn("README", {task["id"] for task in payload["tasks"]})

    def test_validator_accepts_initialized_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            initialized = run_powershell(INIT_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(
                initialized.returncode, 0, initialized.stderr or initialized.stdout
            )

            result = run_powershell(VALIDATE_SCRIPT, "-Workspace", str(workspace))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertIn("validation: ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
