#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_GETSENTRY_GODOT_NATIVE_CACHE_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/cache/getsentry-godot-native-9eb78fc734a5}}"
git_mirror_root="${ECOSYNC_GIT_MIRROR_ROOT:-${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy/tmp/evaluator-dependency-cache/git-mirrors}}"
godot_image="ecosyncbench/deps/getsentry-sentry-godot:9eb78fc734a5"
native_image="ecosyncbench/deps/getsentry-sentry-native:9eb78fc734a5"
host_repo_root="$(pwd -P)"
uid="${ECOSYNC_UID:-$(id -u)}"
gid="${ECOSYNC_GID:-$(id -g)}"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/godot-scons" \
  "$cache_root/native" \
  "$cache_root/pip" \
  "$git_mirror_root/locks"

dependency_fingerprint() {
  local namespace="$1"
  local repo_root="$2"
  shift 2
  {
    printf '%s\0' "$namespace"
    local relative
    for relative in "$@"; do
      printf '%s\0' "$relative"
      if [[ -f "$repo_root/$relative" ]]; then
        sha256sum "$repo_root/$relative" | cut -d ' ' -f1
      else
        printf '<missing>\n'
      fi
    done
  } | sha256sum | cut -c1-20
}

native_venv_fingerprint="$(dependency_fingerprint "$native_image" "$workspace/repos/getsentry/sentry-native" tests/requirements.txt pyproject.toml setup.cfg setup.py)"
native_venv="$cache_root/venvs/native-$native_venv_fingerprint"
mkdir -p "$native_venv"

prepare_native_venv_mount() {
  local mountpoint="$workspace/repos/getsentry/sentry-native/.venv"
  mkdir -p "$mountpoint"
  if [[ ! -w "$mountpoint" ]]; then
    sudo -n chown "$uid:$gid" "$mountpoint"
  fi
  if [[ ! -w "$native_venv" ]]; then
    sudo -n chown "$uid:$gid" "$native_venv"
  fi
}

ensure_image() {
  local image="$1"
  local dockerfile="$2"
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -t "$image" -f "$task_dir/environment/$dockerfile" "$task_dir/environment"
  fi
}

ensure_git_mirror() {
  local url="$1"
  local key mirror lock tmp
  key="$(printf '%s' "$url" | sha256sum | cut -c1-20)"
  mirror="$git_mirror_root/$key.git"
  lock="$git_mirror_root/locks/$key.lock"
  (
    flock 9
    if git --git-dir="$mirror" rev-parse --is-bare-repository >/dev/null 2>&1; then
      exit 0
    fi
    tmp="$mirror.tmp.$$"
    rm -rf "$tmp"
    for attempt in 1 2 3; do
      if timeout 600 git -c http.version=HTTP/1.1 clone --mirror "$url" "$tmp"; then
        mv "$tmp" "$mirror"
        exit 0
      fi
      rm -rf "$tmp"
      sleep $((attempt * 10))
    done
    exit 1
  ) 9>"$lock"
  printf '%s\n' "$mirror"
}

clone_submodule_at() {
  local target="$1"
  local url="$2"
  local commit="$3"
  local mirror
  if [[ -d "$target/.git" ]] && git -C "$target" rev-parse --verify HEAD >/dev/null 2>&1; then
    if [[ "$(git -C "$target" rev-parse HEAD)" == "$commit" ]]; then
      return 0
    fi
  fi
  rm -rf "$target"
  mkdir -p "$(dirname "$target")"
  mirror="$(ensure_git_mirror "$url")"
  git -c protocol.file.allow=always clone -q --no-checkout "file://$mirror" "$target"
  git -C "$target" checkout -q --detach "$commit"
}

native_submodule_git_args() {
  local urls=(
    https://github.com/getsentry/libunwindstack-ndk
    https://github.com/getsentry/breakpad.git
    https://github.com/getsentry/chromium-linux-syscall-support
    https://github.com/getsentry/crashpad.git
    https://chromium.googlesource.com/linux-syscall-support
    https://github.com/getsentry/mini_chromium.git
    https://chromium.googlesource.com/chromium/src/third_party/zlib
  )
  local url mirror
  printf '%s\0' -c protocol.file.allow=always
  for url in "${urls[@]}"; do
    mirror="$(ensure_git_mirror "$url")"
    printf '%s\0' -c "url.file://$mirror.insteadOf=$url"
  done
}

hydrate_godot_submodules() {
  local repo="$workspace/repos/getsentry/sentry-godot"
  local source_repo="$host_repo_root/repos/getsentry/sentry-godot"
  local base_commit="ce1484b449dd06719143d6e4cc2e9b31affe8a3b"

  clone_submodule_at "$repo/modules/gdUnit4" "https://github.com/MikeSchulze/gdUnit4.git" \
    "$(git -C "$source_repo" ls-tree "$base_commit" modules/gdUnit4 | awk '{print $3}')"
  clone_submodule_at "$repo/modules/godot-cpp" "https://github.com/godotengine/godot-cpp" \
    "$(git -C "$source_repo" ls-tree "$base_commit" modules/godot-cpp | awk '{print $3}')"
  clone_submodule_at "$repo/modules/sentry-native" "https://github.com/getsentry/sentry-native.git" \
    "$(git -C "$source_repo" ls-tree "$base_commit" modules/sentry-native | awk '{print $3}')"
  local git_args=()
  mapfile -d '' -t git_args < <(native_submodule_git_args)
  git -C "$repo/modules/sentry-native" "${git_args[@]}" submodule update --init --recursive
  clone_submodule_at "$repo/project/test/util/json_assert" "https://github.com/getsentry/gdunit-json-assert" \
    "$(git -C "$source_repo" ls-tree "$base_commit" project/test/util/json_assert | awk '{print $3}')"
}

hydrate_native_submodules() {
  local native_repo="$workspace/repos/getsentry/sentry-native"
  if [[ ! -f "$native_repo/external/breakpad/src/common/convert_UTF.cc" || ! -f "$native_repo/external/crashpad/CMakeLists.txt" ]]; then
    local git_args=()
    mapfile -d '' -t git_args < <(native_submodule_git_args)
    git -C "$native_repo" "${git_args[@]}" submodule update --init --recursive \
      external/breakpad \
      external/third_party/lss \
      external/crashpad \
      external/libunwindstack-ndk
  fi
}

run_godot() {
  ensure_image "$godot_image" getsentry-sentry-godot.Dockerfile
  hydrate_godot_submodules
  prepare_native_venv_mount
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-3600}" docker run --rm \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/tmp/ecosync-home \
    -e CI=1 \
    -e SCONS_CACHE=/cache/godot-scons \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$native_venv:/workspace/repos/getsentry/sentry-native/.venv" \
    -w /workspace/repos/getsentry/sentry-godot \
    "$godot_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports /cache/godot-scons
      if [[ -f .git ]]; then
        rm -f .git
        git init -q
        git config user.email ecosyncbench@example.invalid
        git config user.name EcosyncBench
        git add .
        git commit -q -m ecosyncbench-matrix-snapshot
      fi
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" ]]; then
        godot --version >/dev/null
        scons --version >/dev/null
        exit 0
      fi
      scons target=editor debug_symbols=no separate_debug_symbols=no
      scons project/addons/gdUnit4
      cp exports/export_presets.cfg project/export_presets.cfg
      find project/addons/sentry/bin/ -name "crashpad_handler" -exec chmod 755 "{}" \; || true
      timeout --kill-after=10s "${ECOSYNC_GODOT_IMPORT_TIMEOUT_SECONDS:-300}" \
        godot --headless --path project/ --import --quit >/tmp/ecosync-godot-import.log 2>&1 || {
        cat /tmp/ecosync-godot-import.log
        exit 1
      }
      if [[ "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        exit 0
      fi
      set +e
      timeout --kill-after=10s "${ECOSYNC_GODOT_TEST_TIMEOUT_SECONDS:-300}" \
        godot --headless --debug --path project/ \
        --script "res://addons/gdUnit4/bin/GdUnitCmdTool.gd" \
        --ignoreHeadlessMode \
        --continue \
        --add test/isolated/test_structured_logs.gd \
        > /tmp/ecosync-godot-test.log 2>&1
      status=$?
      set -e
      cat /tmp/ecosync-godot-test.log
      python /opt/ecosync/godot_to_junit.py \
        /tmp/ecosync-godot-test.log \
        /workspace/.ecosyncbench/test-reports/sentry-godot-hidden.xml
      exit "$status"
    '
}

run_native() {
  ensure_image "$native_image" sentry-native.Dockerfile
  hydrate_native_submodules
  prepare_native_venv_mount
  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-2400}" docker run --rm \
    --network host \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -u "$uid:$gid" \
    -e HOME=/cache/home \
    -e CI=true \
    -e ECOSYNC_DOCKER_PREBUILD_ONLY="${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" \
    -e ECOSYNC_DOCKER_WARMUP_ONLY="${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" \
    -e PIP_CACHE_DIR=/cache/pip \
    -v "$workspace:/workspace" \
    -v "$cache_root:/cache" \
    -v "$native_venv:/workspace/repos/getsentry/sentry-native/.venv" \
    -w /workspace/repos/getsentry/sentry-native \
    "$native_image" \
    bash -lc '
      set -euo pipefail
      mkdir -p /workspace/.ecosyncbench/test-reports /cache/home /cache/pip
      if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        rm -f .git
        git init -q
        git config user.email ecosyncbench@example.invalid
        git config user.name EcosyncBench
        git add -A >/dev/null 2>&1 || true
        git commit --allow-empty -q -m ecosyncbench-matrix-snapshot >/dev/null 2>&1 || true
      fi
      if [[ ! -x .venv/bin/python ]]; then
        python3 -m venv .venv
      fi
      .venv/bin/pip install --upgrade --requirement tests/requirements.txt
      make update-test-discovery
      if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
        .venv/bin/pytest --version >/dev/null
        exit 0
      fi
      mapfile -t log_tests < <(.venv/bin/python - <<'"'"'PY'"'"'
import re
from pathlib import Path
names = sorted(set(re.findall(r"SENTRY_TEST\(([^)]+)\)", Path("tests/unit/test_logs.c").read_text())))
for name in names:
    print(name)
PY
)
      if [[ "${#log_tests[@]}" -eq 0 ]]; then
        echo "No SENTRY_TEST entries found in tests/unit/test_logs.c" >&2
        exit 2
      fi
      args=()
      for name in "${log_tests[@]}"; do
        args+=("tests/test_unit.py::test_unit[${name}]")
      done
      .venv/bin/pytest -vv "${args[@]}" \
        --junitxml=/workspace/.ecosyncbench/test-reports/sentry-native-hidden.xml
    '
}

case "$profile" in
  sentry_godot-hidden)
    run_godot
    ;;
  sentry_native-hidden)
    run_native
    ;;
  *)
    echo "Unknown profile for getsentry_sentry_godot_sentry_native_9eb78fc734a5: $profile" >&2
    exit 2
    ;;
esac
