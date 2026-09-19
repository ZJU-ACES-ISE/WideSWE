#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ecosync_harness


class HiddenPatchTargetTests(unittest.TestCase):
    def test_binary_target_is_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=EcosyncBench",
                    "-c",
                    "user.email=benchmark@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "base",
                ],
                check=True,
            )

            (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
            (repo / "fixture.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\x10\x80")
            subprocess.run(["git", "-C", str(repo), "add", "-N", "fixture.png"], check=True)
            patch = repo / "hidden.patch"
            patch.write_bytes(
                subprocess.run(
                    ["git", "-C", str(repo), "diff", "--binary", "HEAD"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )

            self.assertEqual(
                ecosync_harness.patch_target_files(patch),
                ["fixture.png", "tracked.txt"],
            )

    def test_prefer_clean_overlay_preserves_already_applied_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            workspace = root / "workspace"
            repo = workspace / "repos" / "example" / "repo"
            task.mkdir()
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "old_test.go").write_text("package example\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "old_test.go"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=EcosyncBench",
                    "-c",
                    "user.email=benchmark@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "base",
                ],
                check=True,
            )

            subprocess.run(["git", "-C", str(repo), "mv", "old_test.go", "new_test.go"], check=True)
            patch = task / "hidden.patch"
            patch.write_bytes(
                subprocess.run(
                    ["git", "-C", str(repo), "diff", "--binary", "HEAD"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )

            harness = {
                "hidden_test_patches": [
                    {
                        "repo": "repo",
                        "patch": "hidden.patch",
                        "test_profile": "repo-hidden",
                        "origin": "upstream_test_diff",
                    }
                ]
            }
            task_data = {
                "repositories": [
                    {"name": "repo", "path": "repos/example/repo"}
                ]
            }
            with (
                mock.patch.object(ecosync_harness, "load_harness", return_value=harness),
                mock.patch.object(ecosync_harness, "load_task", return_value=task_data),
                mock.patch.object(ecosync_harness, "task_dir", return_value=task),
            ):
                results = ecosync_harness.apply_hidden(
                    "task",
                    workspace,
                    allow_already_applied=True,
                    profiles=["repo-hidden"],
                    prefer_clean_overlay=True,
                )

            self.assertEqual(results[0]["status"], "already_applied")

    def test_prefer_clean_overlay_replaces_colliding_agent_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            workspace = root / "workspace"
            repo = workspace / "repos" / "example" / "repo"
            task.mkdir()
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            test_file = repo / "feature_test.go"
            test_file.write_text("package example\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", test_file.name], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=EcosyncBench",
                    "-c",
                    "user.email=benchmark@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "base",
                ],
                check=True,
            )

            hidden_content = "package example\n\nfunc TestFeature() { /* hidden */ }\n"
            test_file.write_text(hidden_content, encoding="utf-8")
            patch = task / "hidden.patch"
            patch.write_bytes(
                subprocess.run(
                    ["git", "-C", str(repo), "diff", "--binary", "HEAD"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )
            subprocess.run(["git", "-C", str(repo), "checkout", "--", test_file.name], check=True)
            test_file.write_text(
                "package example\n\nfunc TestFeature() { /* agent */ }\n",
                encoding="utf-8",
            )

            harness = {
                "hidden_test_patches": [
                    {
                        "repo": "repo",
                        "patch": "hidden.patch",
                        "test_profile": "repo-hidden",
                        "origin": "upstream_test_diff",
                    }
                ]
            }
            task_data = {"repositories": [{"name": "repo", "path": "repos/example/repo"}]}
            with (
                mock.patch.object(ecosync_harness, "load_harness", return_value=harness),
                mock.patch.object(ecosync_harness, "load_task", return_value=task_data),
                mock.patch.object(ecosync_harness, "task_dir", return_value=task),
            ):
                results = ecosync_harness.apply_hidden(
                    "task",
                    workspace,
                    allow_already_applied=True,
                    profiles=["repo-hidden"],
                    prefer_clean_overlay=True,
                )

            self.assertEqual(results[0]["status"], "overlay_from_clean_base")
            self.assertEqual(test_file.read_text(encoding="utf-8"), hidden_content)


if __name__ == "__main__":
    unittest.main()
