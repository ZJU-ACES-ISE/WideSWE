#!/usr/bin/env bash
set -euo pipefail

report="${1:?report path required}"
shift

tmp="${report}.tmp"
mkdir -p "$(dirname "$report")"

set +e
npx tap --reporter=junit "$@" > "$tmp"
tap_rc=$?
set -e

awk 'BEGIN { seen = 0 } /^<\?xml/ { seen = 1 } seen { print }' "$tmp" > "$report" || true

if [[ ! -s "$report" ]] || ! grep -q '<testsuites' "$report"; then
  cat "$tmp" >&2
  exit "$tap_rc"
fi

node - "$report" <<'NODE'
const fs = require('fs')
const report = process.argv[2]
const xml = fs.readFileSync(report, 'utf8')
let failures = 0
let errors = 0
for (const match of xml.matchAll(/<testsuite[s]?\b[^>]*>/g)) {
  const tag = match[0]
  const failureMatch = tag.match(/\bfailures="(\d+)"/)
  const errorMatch = tag.match(/\berrors="(\d+)"/)
  if (failureMatch) failures += Number(failureMatch[1])
  if (errorMatch) errors += Number(errorMatch[1])
}
process.exit(failures || errors ? 1 : 0)
NODE
