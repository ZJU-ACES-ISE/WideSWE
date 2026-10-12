from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml
import pull_images
import ecosync_harness
from registry_tools import publish_deps_images


SPEC = importlib.util.spec_from_file_location(
    "prepare_image_release", Path(__file__).resolve().parents[2] / "scripts" / "prepare_image_release.py",
)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
SOURCE_SPEC = importlib.util.spec_from_file_location(
    "fetch_sources", Path(__file__).resolve().parents[2] / "scripts" / "fetch_sources.py",
)
fetch_sources = importlib.util.module_from_spec(SOURCE_SPEC)
SOURCE_SPEC.loader.exec_module(fetch_sources)
DIGEST = "sha256:" + "a" * 64
REF = "ghcr.io/example/project/runtime:version"


class ReleaseImagesTests(unittest.TestCase):
    def test_go_dependency_preparation_is_explicit_and_preserves_offline_default(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with mock.patch.object(ecosync_harness.shutil, "which", return_value="/usr/bin/docker"):
                ecosync_harness.profile_docker_shim_env(workspace, {"PATH": "/usr/bin:/bin"})
            source = (workspace / ".ecosyncbench" / "harness-bin" / "docker").read_text()
            start = source.index("def has_container_env(")
            end = source.index("def option_value(")
            import os
            import shlex
            namespace = {"os": os, "shlex": shlex, "volume_destinations": lambda args: set()}
            exec(source[start:end], namespace)
            function = namespace["evaluator_go_workspace_args"]
            with mock.patch.dict(os.environ, {"ECOSYNC_EVALUATOR_GOWORK": "off"}, clear=True):
                self.assertIn("GOPROXY=off", function([]))
                os.environ["ECOSYNC_EVALUATOR_GOPROXY"] = "https://proxy.golang.org,direct"
                self.assertIn("GOPROXY=https://proxy.golang.org,direct", function([]))
                self.assertFalse(any(value.startswith("GOPROXY=") for value in function(["-e", "GOPROXY=off"])))
                del os.environ["ECOSYNC_EVALUATOR_GOWORK"]
                self.assertEqual(function([]), [])

    def test_profile_runner_receives_actual_code_root(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(ecosync_harness, "task_dir", return_value=workspace), \
                 mock.patch.object(ecosync_harness, "evaluator_node_modules_mounts", return_value=[]), \
                 mock.patch.object(ecosync_harness, "profile_docker_shim_env", side_effect=lambda _, env: env), \
                 mock.patch.object(ecosync_harness, "run_profile_process", return_value=0) as run:
                self.assertEqual(ecosync_harness._run_profile_unlocked("example", workspace, "test"), 0)
            self.assertEqual(run.call_args.args[1]["ECOSYNC_REPO_ROOT"], str(ecosync_harness.REPO_ROOT))

    def test_runners_never_retag_generic_upstream_images(self):
        tasks = (
            ("ipfs", "ipfs_boxo_kubo_97117932efd2", "boxo-hidden"),
            ("mapbox", "mapbox_mcp_devkit_server_mcp_server_ca69b6fe7525", "mcp_devkit_server-hidden"),
            ("elastic", "elastic_elastic_transport_js_elasticsearch_js_b5a8200a9250", "elastic_transport_js-hidden"),
        )
        root = Path(__file__).resolve().parents[2]
        for ecosystem, task, profile in tasks:
            for present in (False, True):
                with self.subTest(task=task, present=present), tempfile.TemporaryDirectory() as temp:
                    directory = Path(temp)
                    docker = directory / "docker"
                    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                                      'if [ "$1" = image ]; then exit "$INSPECT_EXIT"; fi\nexit 0\n')
                    docker.chmod(0o755)
                    log = directory / "calls"
                    env = {"PATH": f"{directory}:/usr/bin:/bin", "HOME": str(directory),
                           "ECOSYNC_WORKSPACE": str(directory), "ECOSYNC_CACHE_ROOT": str(directory / "cache"),
                           "ECOSYNC_DOCKER_PREBUILD_ONLY": "1", "ECOSYNC_DOCKER_BUILD": "0",
                           "CALL_LOG": str(log), "INSPECT_EXIT": "0" if present else "1"}
                    command = root / "cases" / ecosystem / task / "environment/run_profile.sh"
                    result = subprocess.run(["bash", str(command), profile], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0 if present else 1, result.stderr)
                    calls = log.read_text().splitlines()
                    self.assertFalse(any(line.startswith(("pull ", "tag ")) for line in calls))
                    self.assertEqual(len(calls), 2 if present else 1)
                    if not present:
                        self.assertIn(f"--task {task} to restore", result.stderr)

    def test_existing_source_commit_gets_a_persistent_ref_without_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "tasks/case"
            task.mkdir(parents=True)
            commit = "a" * 40
            url = "https://github.com/example/repo.git"
            (task / "repos.yaml").write_text(yaml.safe_dump({"repos": {"repo": {
                "url": url, "local_path": "repos/example/repo", "base_commit": commit,
            }}}))
            destination = root / "sources/example/repo"
            (destination / ".git").mkdir(parents=True)
            with mock.patch("sys.argv", ["fetch_sources", "--tasks-dir", str(root / "tasks"), "--output-dir", str(root / "sources")]), \
                 mock.patch.object(fetch_sources.subprocess, "check_output", return_value=url + "\n"), \
                 mock.patch.object(fetch_sources.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(fetch_sources, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fetch_sources.main(), 0)
            run.assert_called_once_with(["git", "-C", str(destination.resolve()), "update-ref", "refs/wideswe/" + commit, commit])

    def test_source_destination_cannot_escape_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertEqual(fetch_sources.source_destination(root, "repos/org/repo"), root / "org/repo")
            for path in ("/tmp/external", "../external", "repos/../../external", "repos"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    fetch_sources.source_destination(root, path)
            (root / "escape").symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                fetch_sources.source_destination(root, "escape/external")

    def test_runtime_inventory_includes_services_and_compose(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp)
            (env / "test_profiles.yaml").write_text(
                "required_service_images: [postgres:16-alpine]\nprofiles:\n  test:\n    dependency_image: python:3.12\n")
            (env / "docker-compose.yml").write_text("services:\n  redis:\n    image: redis:5.0-alpine\n")
            self.assertEqual(prepare.runtime_images(env, {}), ["postgres:16-alpine", "python:3.12", "redis:5.0-alpine"])

    def test_runtime_inventory_rejects_unresolved_image(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                prepare.runtime_images(Path(temp), {"dependency_images_referenced_by_task": ["${IMAGE}"]})

    def test_bare_digest(self):
        self.assertEqual(pull_images.image_reference({"release_tag": REF, "release_digest": DIGEST}),
                         REF.rsplit(":", 1)[0] + "@" + DIGEST)

    def test_registry_port_is_preserved(self):
        self.assertEqual(pull_images.image_reference({"release_tag": "localhost:5000/repo:v1", "release_digest": DIGEST}),
                         "localhost:5000/repo@" + DIGEST)

    def test_qualified_digest(self):
        full = REF.rsplit(":", 1)[0] + "@" + DIGEST
        self.assertEqual(pull_images.image_reference({"release_tag": REF, "release_digest": full}), full)

    def test_mismatched_and_invalid_digests_fail(self):
        for digest in ("ghcr.io/other/image@" + DIGEST, "sha256:abc", "not-a-digest"):
            with self.subTest(digest=digest), self.assertRaises(SystemExit):
                pull_images.image_reference({"release_tag": REF, "release_digest": digest})

    def test_missing_tag_for_bare_digest_fails(self):
        with self.assertRaises(SystemExit):
            pull_images.image_reference({"release_digest": DIGEST})

    def test_published_requires_digest(self):
        with self.assertRaises(SystemExit):
            pull_images.image_reference({"release_tag": REF, "release_status": "published"})

    def test_legacy_digest_list_selects_matching_repo(self):
        full = REF.rsplit(":", 1)[0] + "@" + DIGEST
        self.assertEqual(pull_images.image_reference({"release_tag": REF, "release_digests": ["other@" + DIGEST, full]}), full)

    def test_legacy_digest_list_rejects_wrong_repo(self):
        with self.assertRaises(SystemExit):
            pull_images.image_reference({"release_tag": REF, "release_digests": ["other@" + DIGEST]})

    @mock.patch.object(pull_images, "run")
    @mock.patch.object(pull_images, "image_exists", return_value=False)
    def test_unpublished_never_pulls(self, exists, run):
        with self.assertRaises(SystemExit):
            pull_images.pull_image_entry("test", {"local_tag": "local:v1", "release_tag": REF,
                                                  "release_status": "not_published"}, dry_run=False)
        run.assert_not_called()

    @mock.patch.object(pull_images, "run")
    @mock.patch.object(pull_images, "image_exists", return_value=False)
    def test_pull_uses_digest_and_restores_local_name(self, exists, run):
        pull_images.pull_image_entry("test", {"local_tag": "local:v1", "release_tag": REF,
                                               "release_digest": DIGEST, "release_status": "published"}, dry_run=False)
        pinned = REF.rsplit(":", 1)[0] + "@" + DIGEST
        self.assertEqual(run.call_args_list, [mock.call(["docker", "pull", pinned], dry_run=False),
                                              mock.call(["docker", "tag", pinned, "local:v1"], dry_run=False)])

    def test_build_parents_ignore_stage_alias_and_external_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "test.Dockerfile").write_text(
                "FROM --platform=linux/amd64 ecosyncbench/base/python:v1 AS base\n"
                "FROM base AS final\nFROM ubuntu:24.04\n", encoding="utf-8")
            self.assertEqual(prepare.build_parents(root), ["ecosyncbench/base/python:v1"])

    def test_release_names_are_anonymous_and_do_not_collide(self):
        self.assertEqual(prepare.release_tag("ecosyncbench/deps/a:v1"), "ghcr.io/code-tmp/wideswe/deps/a:v1")
        self.assertEqual(prepare.release_tag("rust:1.91-bookworm"), "ghcr.io/code-tmp/wideswe/upstream/rust:1.91-bookworm")

    @mock.patch.object(publish_deps_images, "push_archive")
    @mock.patch.object(publish_deps_images, "write_yaml")
    @mock.patch.object(publish_deps_images, "image_inspect", return_value={"Id": "image-id"})
    def test_publisher_defaults_to_read_only(self, inspect, write, push):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = root / "task" / "environment"
            env.mkdir(parents=True)
            (env / "deps_image_lock.yaml").write_text(
                "images:\n  runtime:\n    local_tag: local:v1\n    release_tag: " + REF
                + "\n    local_image_id: image-id\n", encoding="utf-8")
            with mock.patch.object(publish_deps_images, "TASKS_DIR", root):
                result = publish_deps_images.publish_task("task", False)
            self.assertTrue(result["images"]["runtime"]["dry_run"])
        write.assert_not_called()
        push.assert_not_called()

    def test_release120_lock_coverage_and_alias_consistency(self):
        root = Path(__file__).resolve().parents[2]
        locks = sorted((root / "cases").glob("*/*/environment/deps_image_lock.yaml"))
        self.assertEqual(len(locks), 120)
        aliases = {}
        for path in locks:
            with self.subTest(task=path.parent.parent.name):
                lock = yaml.safe_load(path.read_text(encoding="utf-8"))
                case = lock["case_base_image"]
                self.assertEqual(case["release_status"], "published")
                self.assertIn("@sha256:", pull_images.image_reference(case))
                dependencies = {spec["local_tag"] for spec in lock.get("images", {}).values()}
                self.assertTrue(set(prepare.runtime_images(path.parent, case)) <= dependencies)
                self.assertTrue(set(case["dependency_images_referenced_by_task"]) <= dependencies)
                profiles = yaml.safe_load((path.parent / "test_profiles.yaml").read_text(encoding="utf-8"))
                for profile in profiles.get("profiles", {}).values():
                    if profile.get("dependency_image"):
                        self.assertIn(profile["dependency_image"], dependencies)
                parents = {spec["local_tag"] for spec in lock.get("build_parent_images", {}).values()}
                self.assertTrue(set(prepare.build_parents(path.parent)) <= parents | dependencies)
                for spec in [case, *lock.get("images", {}).values(), *lock.get("build_parent_images", {}).values()]:
                    self.assertTrue(spec["release_tag"].startswith("ghcr.io/code-tmp/wideswe/"))
                    local = spec["local_tag"]
                    if local in aliases:
                        self.assertEqual(spec, aliases[local])
                    aliases[local] = spec

    @mock.patch.object(publish_deps_images, "push_archive", return_value=REF.rsplit(":", 1)[0] + "@" + DIGEST)
    @mock.patch.object(publish_deps_images, "image_inspect", return_value={"Id": "image-id", "Size": 10})
    def test_uploaded_image_is_not_declared_public_and_shared_upload_is_deduplicated(self, inspect, push):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for task in ("first", "second"):
                env = root / task / "environment"
                env.mkdir(parents=True)
                (env / "deps_image_lock.yaml").write_text(
                    "images:\n  runtime:\n    local_tag: local:v1\n    release_tag: " + REF
                    + "\n    local_image_id: image-id\n", encoding="utf-8")
            with mock.patch.object(publish_deps_images, "TASKS_DIR", root):
                cache = {}
                for task in ("first", "second"):
                    publish_deps_images.publish_task(task, False, push=True, published=cache)
                    lock = yaml.safe_load((root / task / "environment" / "deps_image_lock.yaml").read_text())
                    self.assertEqual(lock["images"]["runtime"]["release_status"], "not_published")
                    self.assertIn(DIGEST, lock["images"]["runtime"]["release_digest"])
            push.assert_called_once()

    def test_check_env_detects_missing_evaluator_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = root / "environment"
            env.mkdir()
            (env / "deps_image_lock.yaml").write_text(
                "case_base_image:\n  local_tag: case:v1\nimages:\n  runtime:\n    local_tag: dependency:v1\n")
            with mock.patch.object(ecosync_harness, "task_dir", return_value=root), \
                 mock.patch.object(ecosync_harness.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]), \
                 mock.patch.object(ecosync_harness, "capture", return_value=subprocess.CompletedProcess([], 0, stdout='[{"Id":"case-id"}]')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    ecosync_harness.check_env("task")
            report = json.loads(output.getvalue())
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["images"]), 2)
            self.assertFalse(report["images"][1]["present"])

    def test_check_env_rejects_existing_but_different_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = root / "environment"
            env.mkdir()
            (env / "deps_image_lock.yaml").write_text(
                "case_base_image:\n  local_tag: case:v1\n  local_image_id: expected-id\n")
            with mock.patch.object(ecosync_harness, "task_dir", return_value=root), \
                 mock.patch.object(ecosync_harness.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(ecosync_harness, "capture", return_value=subprocess.CompletedProcess([], 0, stdout='[{"Id":"stale-id"}]')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    ecosync_harness.check_env("task")
            report = json.loads(output.getvalue())
            self.assertFalse(report["ok"])
            self.assertTrue(report["images"][0]["present"])
            self.assertFalse(report["images"][0]["matches_lock"])

    def test_check_env_with_compose_still_requires_case_base(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / "environment"
            env.mkdir()
            (env / "docker-compose.yml").write_text("services:\n  test:\n    image: dependency:v1\n")
            (env / "deps_image_lock.yaml").write_text(
                "case_base_image:\n  local_tag: case:v1\nimages:\n  runtime:\n    local_tag: dependency:v1\n")
            with mock.patch.object(ecosync_harness, "task_dir", return_value=Path(temp)), \
                 mock.patch.object(ecosync_harness.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]), \
                 mock.patch.object(ecosync_harness, "capture", return_value=subprocess.CompletedProcess([], 0, stdout='[{"Id":"dep-id"}]')), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    ecosync_harness.check_env("task")
            report = json.loads(output.getvalue())
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["images"]), 2)

    def test_empty_compose_is_not_a_successful_check(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / "environment"
            env.mkdir()
            (env / "docker-compose.yml").write_text("services: {}\n")
            with mock.patch.object(ecosync_harness, "task_dir", return_value=Path(temp)), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    ecosync_harness.check_env("task")


if __name__ == "__main__":
    unittest.main()
