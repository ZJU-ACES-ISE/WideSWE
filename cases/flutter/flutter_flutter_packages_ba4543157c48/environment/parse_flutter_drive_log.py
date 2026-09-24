#!/usr/bin/env python3
"""Convert Flutter web integration-test driver output into JUnit XML."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path


def main() -> int:
    log_path = Path(sys.argv[1])
    test_path = Path(sys.argv[2])
    report_path = Path(sys.argv[3])
    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    source = test_path.read_text(encoding="utf-8", errors="ignore")

    tests: list[str] = []
    for match in re.finditer(r"testWidgets\s*\(\s*(?:r)?([\"'])(.*?)\1", source, re.S):
        name = re.sub(r"\s+", " ", match.group(2)).strip()
        if name and name not in tests:
            tests.append(name)

    failed = set(re.findall(r"Failure in method:\s*(.+)", log_text))
    success = "Application finished." in log_text or "All tests passed" in log_text
    if not tests and (failed or success):
        tests = sorted(failed)
    if not success and not failed:
        raise SystemExit("flutter drive did not produce recognizable integration-test results")

    failures = 0
    cases: list[str] = []
    for name in tests:
        escaped_name = html.escape(name, quote=True)
        if name in failed:
            failures += 1
            cases.append(
                f'  <testcase classname="integration_test.widget_test" name="{escaped_name}">'
                f'<failure message="flutter drive failure">{html.escape(log_text, quote=False)}</failure></testcase>'
            )
        else:
            cases.append(f'  <testcase classname="integration_test.widget_test" name="{escaped_name}" />')

    report_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuite name="packages-pointer-interceptor-drive" tests="{len(tests)}" '
        f'failures="{failures}" errors="0" skipped="0">\n'
        + "\n".join(cases)
        + "\n</testsuite>\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
