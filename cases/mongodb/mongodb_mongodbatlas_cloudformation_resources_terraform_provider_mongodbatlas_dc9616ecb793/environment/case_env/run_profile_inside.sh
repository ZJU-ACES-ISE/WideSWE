#!/usr/bin/env bash
set -euo pipefail
cat >&2 <<'EOF'
ecosync-case-run-profile is disabled in agent case-env images.
Formal benchmark tests are evaluator-only and are injected after agent work.
Use the repository's normal visible test commands for debugging.
EOF
exit 2
