#!/usr/bin/env python3
import html
import re
import sys
import xml.etree.ElementTree as ET


def strip_rich(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\[(?:/?(?:b|i|u)|/?color(?:=[^\]]+)?)\]", "", text)
    text = re.sub(r"Sentry: INFO: LOG:.*$", "", text)
    return text


def main() -> int:
    log_path, out_path = sys.argv[1], sys.argv[2]
    raw = open(log_path, encoding="utf-8", errors="replace").read()
    cases = []
    failures = {}
    seen = set()
    current_suite = None
    current_name = None
    for line in raw.splitlines():
        clean = strip_rich(line).strip()
        match = re.search(r"\b(PASSED|FAILED|SKIPPED)\s*:\s*(.*?)\s*>\s*(.*?)\s*$", clean)
        if match:
            status, suite, name = match.groups()
            key = (suite.strip(), name.strip())
        else:
            suite_match = re.search(r"(res://[^\s]+\.gd)\b", clean)
            if suite_match:
                current_suite = suite_match.group(1)
                continue
            name_match = re.fullmatch(r"(test_[A-Za-z0-9_]+)", clean)
            if name_match:
                current_name = name_match.group(1)
                continue
            status_match = re.fullmatch(r"(PASSED|FAILED|SKIPPED)", clean)
            if not status_match or not current_suite or not current_name:
                continue
            status = status_match.group(1)
            key = (current_suite, current_name)

        if key not in seen:
            cases.append((key[0], key[1], status.lower()))
            seen.add(key)
        if status == "FAILED":
            failures[key] = clean
        current_name = None

    testsuite = ET.Element("testsuite", {
        "name": "sentry-godot-hidden",
        "tests": str(len(cases)),
        "failures": str(sum(1 for _, _, status in cases if status == "failed")),
        "errors": "0",
        "skipped": str(sum(1 for _, _, status in cases if status == "skipped")),
    })
    for suite, name, status in cases:
        testcase = ET.SubElement(testsuite, "testcase", {
            "classname": suite,
            "name": name,
        })
        if status == "failed":
            failure = ET.SubElement(testcase, "failure", {"message": "gdUnit test failed"})
            failure.text = html.escape(failures.get((suite, name), "gdUnit test failed"))
        elif status == "skipped":
            ET.SubElement(testcase, "skipped")

    tree = ET.ElementTree(testsuite)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    if not cases:
        print("No gdUnit test cases were parsed from Godot output", file=sys.stderr)
        return 105
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
