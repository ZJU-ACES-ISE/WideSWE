#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import time
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import requests


DOCKER_MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_CONFIG = "application/vnd.docker.container.image.v1+json"
DOCKER_LAYER_GZIP = "application/vnd.docker.image.rootfs.diff.tar.gzip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def gzip_file(src: Path, dst: Path) -> None:
    with src.open("rb") as input_file, gzip.GzipFile(filename="", mode="wb", fileobj=dst.open("wb"), mtime=0) as output_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            output_file.write(chunk)


def parse_image_ref(ref: str) -> tuple[str, str, str]:
    if "://" in ref:
        raise SystemExit(f"invalid image ref: {ref}")
    name, sep, tag = ref.rpartition(":")
    if not sep or "/" not in name:
        raise SystemExit(f"expected tagged image ref: {ref}")
    registry, repo = name.split("/", 1)
    return registry, repo, tag


def docker_auth(registry: str) -> tuple[str, str] | None:
    config_path = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    if not config_path.exists():
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    auths = config.get("auths") or {}
    candidates = [registry, f"https://{registry}", f"http://{registry}"]
    for key in candidates:
        entry = auths.get(key)
        if not entry:
            continue
        if entry.get("auth"):
            decoded = base64.b64decode(entry["auth"]).decode("utf-8")
            username, _, password = decoded.partition(":")
            return username, password
        if entry.get("username") and entry.get("password"):
            return entry["username"], entry["password"]
    return None


def bearer_token(session: requests.Session, registry: str, repo: str) -> str:
    auth = docker_auth(registry)
    params = {
        "service": registry,
        "scope": f"repository:{repo}:pull,push",
    }
    last_error: Exception | None = None
    retries = int(os.environ.get("ECOSYNC_REGISTRY_RETRIES", "3"))
    for attempt in range(1, retries + 1):
        try:
            response = session.get(f"https://{registry}/token", params=params, auth=auth, timeout=60)
            response.raise_for_status()
            token = response.json().get("token")
            if not token:
                raise SystemExit("registry token response did not include token")
            return token
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_s = min(30, 2**attempt)
            print(f"retry registry token {attempt} after {sleep_s}s: {exc}", flush=True)
            time.sleep(sleep_s)
    if last_error:
        raise last_error
    raise SystemExit("registry token request failed")


def registry_url(registry: str, repo: str, suffix: str) -> str:
    quoted_repo = "/".join(quote(part, safe="") for part in repo.split("/"))
    return f"https://{registry}/v2/{quoted_repo}{suffix}"


def ensure_blob(session: requests.Session, registry: str, repo: str, digest: str, path: Path) -> None:
    head = session.head(registry_url(registry, repo, f"/blobs/{digest}"), timeout=60)
    if head.status_code == 200:
        print(f"existing blob: {digest}", flush=True)
        return
    if head.status_code not in {404, 401, 403}:
        head.raise_for_status()

    start = session.post(registry_url(registry, repo, "/blobs/uploads/"), timeout=60)
    start.raise_for_status()
    location = start.headers["Location"]
    if location.startswith("/"):
        location = f"https://{registry}{location}"
    separator = "&" if "?" in location else "?"
    upload_url = f"{location}{separator}digest={quote(digest, safe=':')}"
    print(f"uploading blob: {digest} size={path.stat().st_size}", flush=True)
    last_error: Exception | None = None
    for attempt in range(1, int(os.environ.get("ECOSYNC_REGISTRY_RETRIES", "3")) + 1):
        try:
            with path.open("rb") as handle:
                put = session.put(
                    upload_url,
                    data=handle,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=None,
                )
            put.raise_for_status()
            print(f"pushed blob: {digest}", flush=True)
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= int(os.environ.get("ECOSYNC_REGISTRY_RETRIES", "3")):
                break
            sleep_s = min(30, 2**attempt)
            print(f"retry blob upload {attempt}: {digest} after {sleep_s}s: {exc}", flush=True)
            time.sleep(sleep_s)
    if last_error:
        raise last_error


def read_archive(archive: Path, workdir: Path) -> tuple[dict, Path, list[Path]]:
    with tarfile.open(archive) as tar:
        manifest_member = tar.extractfile("manifest.json")
        if manifest_member is None:
            raise SystemExit(f"{archive} does not contain manifest.json")
        manifest = json.loads(manifest_member.read().decode("utf-8"))
        if len(manifest) != 1:
            raise SystemExit("expected archive with exactly one image")
        entry = manifest[0]

        config_rel = entry["Config"]
        config_path = workdir / "config.json"
        config_member = tar.extractfile(config_rel)
        if config_member is None:
            raise SystemExit(f"missing config blob in archive: {config_rel}")
        config_path.write_bytes(config_member.read())

        layers = []
        for index, layer_rel in enumerate(entry["Layers"]):
            layer_member = tar.extractfile(layer_rel)
            if layer_member is None:
                raise SystemExit(f"missing layer in archive: {layer_rel}")
            raw_path = workdir / f"layer-{index}.tar"
            raw_path.write_bytes(layer_member.read())
            layers.append(raw_path)
        return entry, config_path, layers


def push_archive(archive: Path, image_ref: str) -> str:
    registry, repo, tag = parse_image_ref(image_ref)
    with tempfile.TemporaryDirectory(prefix="ecosync-registry-push-") as temp:
        workdir = Path(temp)
        _entry, config_path, raw_layers = read_archive(archive, workdir)
        config_digest = sha256_file(config_path)
        config_size = config_path.stat().st_size

        layer_entries = []
        for index, raw_layer in enumerate(raw_layers):
            gz_path = workdir / f"layer-{index}.tar.gz"
            gzip_file(raw_layer, gz_path)
            layer_entries.append(
                {
                    "path": gz_path,
                    "digest": sha256_file(gz_path),
                    "size": gz_path.stat().st_size,
                }
            )

        manifest = {
            "schemaVersion": 2,
            "mediaType": DOCKER_MANIFEST_V2,
            "config": {
                "mediaType": DOCKER_CONFIG,
                "size": config_size,
                "digest": config_digest,
            },
            "layers": [
                {
                    "mediaType": DOCKER_LAYER_GZIP,
                    "size": item["size"],
                    "digest": item["digest"],
                }
                for item in layer_entries
            ],
        }
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()

        session = requests.Session()
        session.trust_env = os.environ.get("ECOSYNC_REGISTRY_USE_PROXY") == "1"
        token = bearer_token(session, registry, repo)
        session.headers.update({"Authorization": f"Bearer {token}"})

        ensure_blob(session, registry, repo, config_digest, config_path)
        for item in layer_entries:
            ensure_blob(session, registry, repo, item["digest"], item["path"])

        response = session.put(
            registry_url(registry, repo, f"/manifests/{quote(tag, safe='')}"),
            data=manifest_bytes,
            headers={"Content-Type": DOCKER_MANIFEST_V2},
            timeout=120,
        )
        response.raise_for_status()
        print(f"pushed manifest: {image_ref}@{manifest_digest}", flush=True)
        return f"{image_ref.rsplit(':', 1)[0]}@{manifest_digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Push a docker save archive through the registry HTTP API.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("image")
    args = parser.parse_args()
    print(push_archive(args.archive, args.image))


if __name__ == "__main__":
    main()
