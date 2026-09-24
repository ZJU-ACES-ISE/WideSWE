#!/usr/bin/env bash
set -euo pipefail

profile="${1:?profile required}"
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$(cd "${ECOSYNC_WORKSPACE:-$task_dir}" && pwd)"
cache_root="${ECOSYNC_CACHE_ROOT:-/tmp/ecosyncbench-release120-runtime/cache/legacy}"
case_cache="flutter-ba4543157c48"
flutter_framework_version="3.33.0-1.0.pre.191"
flutter_git_version="3.33.0-0.0.pre-191-gd5ae783962"
flutter_commit_date="2025-05-23 07:40:20 -0400"
flutter_base_commit="$(awk '
  /^  flutter:$/ { in_flutter = 1; next }
  in_flutter && /^  [^ ]/ { in_flutter = 0 }
  in_flutter && /^[[:space:]]+base_commit:/ { print $2; exit }
' "$task_dir/repos.yaml")"

if [[ ! "$flutter_base_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Unable to read the Flutter base commit from $task_dir/repos.yaml" >&2
  exit 2
fi

image_flutter="ecosyncbench/deps/flutter-web:a9d88b259153"

mkdir -p \
  "$workspace/.ecosyncbench/test-reports" \
  "$cache_root/flutter-cache/$case_cache/bin-cache" \
  "$cache_root/flutter-cache/$case_cache/test-seeds" \
  "$cache_root/pub-cache/$case_cache"

ensure_flutter_image() {
  if [[ "${ECOSYNC_DOCKER_BUILD:-0}" == "1" ]] || ! docker image inspect "$image_flutter" >/dev/null 2>&1; then
    docker build \
      -t "$image_flutter" \
      -f "$task_dir/environment/flutter-web.Dockerfile" \
      "$task_dir/environment"
  fi
}

git_mounts() {
  local git_file git_dir common_dir common_abs
  [[ -d "$workspace/repos" ]] || return 0
  while IFS= read -r git_file; do
    if [[ -d "$git_file" ]]; then
      printf '%s\0' "$git_file:$git_file:ro"
      continue
    fi
    git_dir="$(sed -n 's/^gitdir: //p' "$git_file" | head -1)"
    [[ "$git_dir" == /* && -d "$git_dir" ]] || continue
    printf '%s\0' "$git_dir:$git_dir:ro"
    if [[ -f "$git_dir/commondir" ]]; then
      common_dir="$(sed -n '1p' "$git_dir/commondir")"
      if [[ "$common_dir" == /* ]]; then
        common_abs="$common_dir"
      else
        common_abs="$(cd "$git_dir/$common_dir" && pwd)"
      fi
      [[ -d "$common_abs" ]] && printf '%s\0' "$common_abs:$common_abs:ro"
    fi
  done < <(find "$workspace/repos" -maxdepth 4 \( -type f -o -type d \) -name .git 2>/dev/null)
}

flutter_tools_source_clean() {
  local flutter_repo="$workspace/repos/flutter/flutter"
  local untracked
  git -C "$flutter_repo" diff --quiet -- packages/flutter_tools || return 1
  git -C "$flutter_repo" diff --cached --quiet -- packages/flutter_tools || return 1
  untracked="$(
    git -C "$flutter_repo" ls-files --others --exclude-standard packages/flutter_tools \
      | grep -Ev '^packages/flutter_tools/(\.dart_tool/|pubspec\.lock$)' || true
  )"
  [[ -z "$untracked" ]]
}

prepare_flutter_tool_cache() {
  local flutter_repo="$workspace/repos/flutter/flutter"
  local cache_dir="$cache_root/flutter-cache/$case_cache/bin-cache"
  local tree_marker="$cache_dir/flutter_tools.tree"
  local tree_revision head_revision
  [[ -e "$flutter_repo/.git" ]] || return 0
  tree_revision="$(git -C "$flutter_repo" rev-parse HEAD:packages/flutter_tools 2>/dev/null || true)"
  head_revision="$(git -C "$flutter_repo" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$tree_revision" && -n "$head_revision" && -f "$cache_dir/flutter_tools.snapshot" && \
        -f "$tree_marker" && "$(cat "$tree_marker")" == "$tree_revision" ]] && \
        flutter_tools_source_clean; then
    restore_test_seed flutter-tools "$flutter_repo/packages/flutter_tools"
    touch "$flutter_repo/packages/flutter_tools/pubspec.lock"
    printf '%s:\n' "$head_revision" >"$cache_dir/flutter_tools.stamp"
  fi
}

prepare_flutter_metadata() {
  local flutter_repo="$workspace/repos/flutter/flutter"
  local framework_tag
  [[ -e "$flutter_repo/.git" ]] || return 0
  framework_tag="$(git -C "$flutter_repo" config --get ecosync.flutterFrameworkTag || true)"
  if [[ -n "$framework_tag" ]] && ! git -C "$flutter_repo" describe --match '*.*.*' --tags HEAD >/dev/null 2>&1; then
    git -C "$flutter_repo" tag "$framework_tag" HEAD
  fi
}

prepare_flutter_version_cache() {
  local version_file="$cache_root/flutter-cache/$case_cache/bin-cache/flutter.version.json"
  python3 - "$version_file" "$flutter_framework_version" "$flutter_git_version" \
    "$flutter_base_commit" "$flutter_commit_date" <<'PY'
import json
import sys
from pathlib import Path

path, framework_version, git_version, revision, commit_date = sys.argv[1:]
payload = {
    "frameworkVersion": framework_version,
    "channel": "[user-branch]",
    "repositoryUrl": "unknown source",
    "frameworkRevision": revision,
    "frameworkCommitDate": commit_date,
    "engineRevision": revision,
    "engineCommitDate": commit_date,
    "dartSdkVersion": "3.9.0 (build 3.9.0-164.0.dev)",
    "devToolsVersion": "2.46.0",
    "flutterVersion": git_version,
}
Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

restore_test_seed() {
  local seed_name="$1"
  local target="$2"
  local seed_dir="$cache_root/flutter-cache/$case_cache/test-seeds/$seed_name"
  [[ -f "$seed_dir/.complete" ]] || return 0
  mkdir -p "$target"
  cp -a --reflink=auto "$seed_dir/." "$target/"
  rm -f "$target/.complete"
}

record_flutter_tool_cache() {
  local flutter_repo="$workspace/repos/flutter/flutter"
  local cache_dir="$cache_root/flutter-cache/$case_cache/bin-cache"
  local tree_revision
  [[ -e "$flutter_repo/.git" && -s "$cache_dir/flutter_tools.snapshot" ]] || return 0
  flutter_tools_source_clean || return 0
  tree_revision="$(git -C "$flutter_repo" rev-parse HEAD:packages/flutter_tools 2>/dev/null || true)"
  [[ -n "$tree_revision" ]] && printf '%s\n' "$tree_revision" >"$cache_dir/flutter_tools.tree"
}

docker_common_args=(
  --rm
  -u "${ECOSYNC_UID:-$(id -u)}:${ECOSYNC_GID:-$(id -g)}"
  -e HOME=/tmp/ecosync-home
  -e CI=1
  -e FORCE_COLOR=0
  -e PUB_CACHE=/pub-cache
  -e FLUTTER_ROOT=/workspace/repos/flutter/flutter
  -e "FLUTTER_PREBUILT_ENGINE_VERSION=$flutter_base_commit"
  -e FLUTTER_TEST_DISABLE_FS_GUARD=true
  -e CHROME_EXECUTABLE=/usr/bin/chromium
  -v "$workspace:/workspace"
  -v "$task_dir/environment:/ecosync-env:ro"
  -v "$cache_root/flutter-cache/$case_cache/bin-cache:/workspace/repos/flutter/flutter/bin/cache"
  -v "$cache_root/pub-cache/$case_cache:/pub-cache"
)

docker_mount_args() {
  local mount_spec
  while IFS= read -r -d '' mount_spec; do
    printf '%s\0%s\0' -v "$mount_spec"
  done < <(git_mounts)
}

run_flutter_widget_test() {
  ensure_flutter_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_flutter" bash -lc 'git --version >/dev/null && chromium --version >/dev/null'
    return 0
  fi
  prepare_flutter_metadata
  prepare_flutter_version_cache
  prepare_flutter_tool_cache
  restore_test_seed flutter "$workspace/repos/flutter/flutter/packages/flutter"

  local -a mounts=()
  local arg
  while IFS= read -r -d '' arg; do
    mounts+=("$arg")
  done < <(docker_mount_args)

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run \
    "${docker_common_args[@]}" \
    "${mounts[@]}" \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -w /workspace/repos/flutter/flutter/packages/flutter \
    "$image_flutter" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      ../../bin/flutter --version
      ../../bin/flutter pub get --offline || ../../bin/flutter pub get
      ../../bin/flutter test \
        --no-pub \
        --platform chrome \
        --machine \
        test/widgets/selectable_region_context_menu_test.dart \
        | tee /workspace/.ecosyncbench/test-reports/flutter-selectable-region-context-menu.jsonl
      test -s /workspace/.ecosyncbench/test-reports/flutter-selectable-region-context-menu.jsonl
    '
  record_flutter_tool_cache
}

run_pointer_interceptor_web_test() {
  ensure_flutter_image
  if [[ "${ECOSYNC_DOCKER_PREBUILD_ONLY:-0}" == "1" || "${ECOSYNC_DOCKER_WARMUP_ONLY:-0}" == "1" ]]; then
    docker run --rm "$image_flutter" bash -lc 'git --version >/dev/null && chromium --version >/dev/null'
    return 0
  fi
  prepare_flutter_metadata
  prepare_flutter_version_cache
  prepare_flutter_tool_cache
  restore_test_seed packages "$workspace/repos/flutter/packages/packages/pointer_interceptor/pointer_interceptor_web/example"

  local -a mounts=()
  local arg
  while IFS= read -r -d '' arg; do
    mounts+=("$arg")
  done < <(docker_mount_args)

  timeout "${ECOSYNC_PROFILE_TIMEOUT_SECONDS:-1800}" docker run \
    "${docker_common_args[@]}" \
    "${mounts[@]}" \
    --name "ecosync_${profile}_$$_${RANDOM}" \
    -w /workspace/repos/flutter/packages/packages/pointer_interceptor/pointer_interceptor_web/example \
    "$image_flutter" \
    bash -lc '
      set -euo pipefail
      mkdir -p /tmp/ecosync-home /workspace/.ecosyncbench/test-reports
      log=/workspace/.ecosyncbench/test-reports/packages-pointer-interceptor-drive.log
      report=/workspace/.ecosyncbench/test-reports/packages-pointer-interceptor-drive.xml
      ../../../../../flutter/bin/flutter pub get --offline || \
        ../../../../../flutter/bin/flutter pub get
      chromedriver --port=4444 --allowed-ips=127.0.0.1 \
        >/workspace/.ecosyncbench/test-reports/chromedriver.log 2>&1 &
      driver_pid=$!
      trap "kill $driver_pid 2>/dev/null || true" EXIT
      sleep 1
      set +e
      ../../../../../flutter/bin/flutter drive \
        --no-pub \
        -d web-server \
        --web-port=7357 \
        --browser-name=chrome \
        --driver=test_driver/integration_test.dart \
        --target=integration_test/widget_test.dart \
        2>&1 | tee "$log"
      status=${PIPESTATUS[0]}
      set -e
      python3 /ecosync-env/parse_flutter_drive_log.py "$log" integration_test/widget_test.dart "$report"
      test -s "$report"
      exit "$status"
    '
  record_flutter_tool_cache
}

case "$profile" in
  flutter-hidden)
    run_flutter_widget_test
    ;;
  packages-hidden)
    run_pointer_interceptor_web_test
    ;;
  *)
    echo "Unknown profile for flutter_flutter_packages_ba4543157c48: ${profile}" >&2
    exit 2
    ;;
esac
