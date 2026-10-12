#!/usr/bin/env python3
"""Prepare image locks locally; never push images or use registry credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "ghcr.io/code-tmp/wideswe"
ACCEPT = ", ".join((
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def release_tag(local: str) -> str:
    if local.startswith("ecosyncbench/"):
        return PREFIX + "/" + local.removeprefix("ecosyncbench/")
    return PREFIX + "/upstream/" + local


def build_parents(environment: Path) -> list[str]:
    refs = set()
    files = list(environment.glob("*Dockerfile*"))
    files.extend((environment / "case_env").glob("Dockerfile*"))
    for path in files:
        stages = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not re.match(r"^\s*FROM\s", line, re.I):
                continue
            parts = shlex.split(line, comments=True)
            args = [part for part in parts[1:] if not part.startswith("--")]
            if not args:
                continue
            parent = args[0]
            if parent.startswith("ecosyncbench/") and parent not in stages:
                refs.add(parent)
            if len(args) >= 3 and args[1].lower() == "as":
                stages.add(args[2])
    return sorted(refs)


def inspect_image(ref: str) -> dict:
    raw = subprocess.check_output(
        ["docker", "image", "inspect", ref], text=True, stderr=subprocess.DEVNULL,
    )
    image = json.loads(raw)[0]
    return {"local_image_id": image["Id"], "size_bytes": image["Size"]}


def runtime_images(environment: Path, case: dict) -> list[str]:
    refs = set(case.get("dependency_images_referenced_by_task", []))
    profiles_file = environment / "test_profiles.yaml"
    if profiles_file.exists():
        profiles = yaml.safe_load(profiles_file.read_text(encoding="utf-8")) or {}
        refs.update(profiles.get("required_service_images", []))
        for profile in profiles.get("profiles", {}).values():
            if profile.get("dependency_image"):
                refs.add(profile["dependency_image"])
    for filename in ("docker-compose.yml", "docker-compose.yaml"):
        compose_file = environment / filename
        if compose_file.exists():
            compose = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
            for service in compose.get("services", {}).values():
                if service.get("image"):
                    refs.add(service["image"])
    if any(not isinstance(ref, str) or "$" in ref or any(c.isspace() for c in ref) for ref in refs):
        raise ValueError("Runtime images must be concrete image references")
    return sorted(refs)


def public_manifest(ref: str) -> tuple[str, str]:
    """Verify a manifest using an anonymous GHCR pull token, not Docker login."""
    if not ref.startswith("ghcr.io/"):
        raise ValueError("Only GHCR release references are supported")
    repo, tag = ref.removeprefix("ghcr.io/").rsplit(":", 1)
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repo}:pull"})
    with urllib.request.urlopen("https://ghcr.io/token?" + query, timeout=20) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/manifests/{tag}",
        headers={"Authorization": "Bearer " + token, "Accept": ACCEPT},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        advertised = response.headers.get("Docker-Content-Digest")
        if advertised and advertised != digest:
            raise ValueError("Registry manifest digest mismatch")
    manifest = json.loads(body)
    if "config" not in manifest:
        raise ValueError("Expected a single-platform release manifest, not an image index")
    return digest, manifest["config"]["digest"]


def verified_manifest(ref: str) -> tuple[str, str]:
    for attempt in range(3):
        try:
            return public_manifest(ref)
        except (OSError, ValueError, KeyError):
            if attempt == 2:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", help="Default: all tasks in release120.tsv")
    parser.add_argument("--verify-case-bases", action="store_true")
    parser.add_argument("--verify-all", action="store_true", help="Also check dependency and build-parent images")
    parser.add_argument("--write-locks", action="store_true", help="Write local YAML only after all requested checks pass")
    args = parser.parse_args()
    with (ROOT / "release120.tsv").open(encoding="utf-8", newline="") as handle:
        tasks = {row["case_id"]: row["ecosystem"] for row in csv.DictReader(handle, delimiter="\t")}
    selected = args.task or list(tasks)
    unknown = set(selected) - tasks.keys()
    if unknown:
        parser.error("Unknown task ID")
    inspections = {}
    verified = {}
    pending = {}
    updates = []
    for index, task in enumerate(selected, 1):
        path = ROOT / "cases" / tasks[task] / task / "environment" / "deps_image_lock.yaml"
        lock = yaml.safe_load(path.read_text(encoding="utf-8"))
        case = lock["case_base_image"]
        dependencies = runtime_images(path.parent, case)
        parents = sorted(set(build_parents(path.parent)) - set(dependencies))
        for field, refs in (("images", dependencies), ("build_parent_images", parents)):
            existing = {spec["local_tag"]: spec for spec in lock.get(field, {}).values()}
            lock[field] = {
                f"image-{i:03d}": existing.get(ref, {
                    "local_tag": ref, "release_tag": release_tag(ref), "release_status": "not_published",
                })
                for i, ref in enumerate(refs, 1)
            }
        entries = [("case_base_image", case)]
        entries.extend(("images", value) for value in lock["images"].values())
        entries.extend(("build_parent_images", value) for value in lock["build_parent_images"].values())
        for kind, spec in entries:
            local = spec["local_tag"]
            remote = spec["release_tag"]
            inspect_ref = remote if kind == "case_base_image" else local
            if inspect_ref not in inspections:
                inspections[inspect_ref] = inspect_image(inspect_ref)
            inspected = inspections[inspect_ref]
            if spec.get("local_image_id") and spec["local_image_id"] != inspected["local_image_id"]:
                raise SystemExit(f"Local image changed since lock preparation: {local}")
            spec.update(inspected)
            if args.verify_all or (args.verify_case_bases and kind == "case_base_image"):
                if remote not in verified:
                    verified[remote] = verified_manifest(remote)
                digest, config_id = verified[remote]
                if config_id != spec["local_image_id"]:
                    raise SystemExit(f"Published image differs from local image: {remote}")
                spec["release_digest"] = digest
                spec["release_status"] = "published"
            elif spec.get("release_status") != "published":
                pending[remote] = {"local_tag": local, **inspections[inspect_ref]}
        updates.append((path, lock))
        print(f"[{index}/{len(selected)}] {task}: checked", flush=True)
    if args.write_locks:
        for path, lock in updates:
            path.write_text(yaml.safe_dump(lock, sort_keys=False, width=120), encoding="utf-8")
    print(json.dumps({
        "tasks": len(updates), "verified_public_images": len(verified),
        "pending_publication": len(pending), "images": pending,
        "locks_written": args.write_locks,
    }, indent=2))


if __name__ == "__main__":
    main()
