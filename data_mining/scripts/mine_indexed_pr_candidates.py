#!/usr/bin/env python3
"""Mine cross-repo linked PR candidates from retained ecosystem organizations.

Input is final_ecosystems.json produced by filter_top200_ecosystems.py. For
each retained ecosystem, the script treats the `org` field as the GitHub
organization, enumerates its active repositories, scans merged PRs since the
configured date, and keeps a candidate only when a PR links to another PR/issue
inside the same ecosystem repository set.

No shared-ticket or proposal clustering is used in this strict pipeline.
Every ecosystem-involved PR must be merged and must have a changed test-file
signal. External PR/issue links are preserved as audit evidence, not used as
hard exclusions.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ECOSYSTEMS = ROOT / "data_mining" / "data" / "ecosystems" / "final_ecosystems.json"
DEFAULT_OUT_DIR = ROOT / "data_mining" / "data" / "cases" / "pr_mining_all_repos_since2024"


GITHUB_LINK_RE = re.compile(
    r"(?:https://github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:(?:/(?P<link_kind>pull|issues)/)|#)(?P<number>\d+)"
)
TEST_PATH_RE = re.compile(
    r"(^|/)(test|tests|spec|specs|__tests__|testing)(/|$)|"
    r"(_test\.|\.test\.|\.spec\.)|"
    r"(Test\.|Tests\.)",
    re.IGNORECASE,
)
IOS_REPO_RE = re.compile(r"(^|[-_/\.])ios($|[-_/\.])", re.IGNORECASE)
IOS_PATH_RE = re.compile(
    r"(^|/)(ios|iosapp|iphone|ipad|xcode|cocoapods)(/|$|[-_.])|"
    r"(\.podspec$)|"
    r"(^|/)(Podfile|Podfile\.lock)$",
    re.IGNORECASE,
)
IOS_TITLE_RE = re.compile(r"\b(iOS|iPhone|iPad|Xcode|CocoaPods)\b")


@dataclass(frozen=True)
class RepoRef:
    ecosystem: str
    org: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.repo}"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_ecosystems(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return rows


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "WIDESWE-data-mining",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get_json(url: str, sleep_seconds: float, retries: int) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, headers=github_headers()), timeout=45) as resp:
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            last_exc = exc
            if exc.code in {403, 429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(min(60, 5 * (2**attempt)))
                continue
            raise
        except (URLError, http.client.RemoteDisconnected, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(30, 3 * (2**attempt)))
                continue
            raise
    raise RuntimeError(f"GitHub request failed: {url}") from last_exc


def parse_github_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_since(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    parsed = parse_github_datetime(value)
    if parsed is None:
        raise SystemExit(f"invalid --since value: {value}")
    return parsed


def list_org_repositories(
    org: str,
    sleep_seconds: float,
    retries: int,
    *,
    include_archived: bool,
    include_forks: bool,
    max_pages: int,
) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
        # /users/{owner}/repos works for both GitHub organizations and user
        # accounts, which keeps the 103-owner input file usable as-is.
        url = f"https://api.github.com/users/{org}/repos?type=public&sort=full_name&per_page=100&page={page}"
        data = github_get_json(url, sleep_seconds, retries)
        if not data:
            break
        for item in data:
            if item.get("disabled"):
                continue
            if item.get("archived") and not include_archived:
                continue
            if item.get("fork") and not include_forks:
                continue
            repos.append(item)
        if len(data) < 100:
            break
        page += 1
    return repos


def iter_merged_prs_since(
    repo: RepoRef,
    since_dt: datetime,
    max_prs: int,
    sleep_seconds: float,
    retries: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Return merged PRs whose merge timestamp is on or after since_dt.

    GitHub's Pulls API can sort closed PRs by updated_at. Because merging a PR
    updates it, once updated_at falls before since_dt, later pages cannot contain
    PRs merged after since_dt.
    """
    prs: list[dict[str, Any]] = []
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
        url = f"https://api.github.com/repos/{repo.full_name}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}"
        data = github_get_json(url, sleep_seconds, retries)
        if not data:
            break
        page_has_possible_recent = False
        for item in data:
            updated_at = parse_github_datetime(item.get("updated_at"))
            if updated_at and updated_at >= since_dt:
                page_has_possible_recent = True
            merged_at = parse_github_datetime(item.get("merged_at"))
            if not merged_at or merged_at < since_dt:
                continue
            prs.append(item)
            if max_prs and len(prs) >= max_prs:
                return prs
        if len(data) < 100 or not page_has_possible_recent:
            break
        page += 1
    return prs


def fetch_pr_metadata(full_name: str, number: int, sleep_seconds: float, retries: int) -> tuple[dict[str, Any] | None, str]:
    url = f"https://api.github.com/repos/{full_name}/pulls/{number}"
    try:
        data = github_get_json(url, sleep_seconds, retries)
    except HTTPError as exc:
        if exc.code in {403, 429, 500, 502, 503, 504}:
            return None, f"TRANSIENT HTTP {exc.code}"
        if exc.code == 404:
            return None, "HTTP 404"
        return None, f"HTTP {exc.code}"
    except (URLError, RuntimeError, http.client.RemoteDisconnected, TimeoutError) as exc:
        return None, f"TRANSIENT {exc}"
    return data, ""


def summarize_pr_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    base = metadata.get("base") or {}
    head = metadata.get("head") or {}
    return {
        "title": metadata.get("title"),
        "state": metadata.get("state"),
        "merged_at": metadata.get("merged_at"),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "closed_at": metadata.get("closed_at"),
        "base_sha": base.get("sha"),
        "head_sha": head.get("sha"),
        "merge_commit_sha": metadata.get("merge_commit_sha"),
        "html_url": metadata.get("html_url"),
    }


def pr_timeline_item(full_name: str, pr_number: int, role: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": full_name,
        "pr_number": pr_number,
        "role": role,
        "title": metadata.get("title"),
        "html_url": metadata.get("html_url"),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "closed_at": metadata.get("closed_at"),
        "merged_at": metadata.get("merged_at"),
        "base_sha": metadata.get("base_sha"),
        "head_sha": metadata.get("head_sha"),
        "merge_commit_sha": metadata.get("merge_commit_sha"),
    }


def fetch_pr_files(full_name: str, number: int, sleep_seconds: float, retries: int, max_pages: int = 3) -> tuple[list[dict[str, Any]], str]:
    files: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = f"https://api.github.com/repos/{full_name}/pulls/{number}/files?per_page=100&page={page}"
        try:
            data = github_get_json(url, sleep_seconds, retries)
        except HTTPError as exc:
            if exc.code in {403, 429, 500, 502, 503, 504}:
                return files, f"TRANSIENT HTTP {exc.code}"
            return files, f"HTTP {exc.code}"
        except (URLError, RuntimeError, http.client.RemoteDisconnected, TimeoutError) as exc:
            return files, f"TRANSIENT {exc}"
        if not data:
            break
        files.extend(data)
        if len(data) < 100:
            break
    return files, ""


def is_transient_file_error(error: str) -> bool:
    return error.startswith("TRANSIENT")


def is_transient_api_error(error: str) -> bool:
    return error.startswith("TRANSIENT")


def summarize_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    names = [item.get("filename", "") for item in files]
    test_files = [
        item.get("filename", "")
        for item in files
        if item.get("status") != "removed" and TEST_PATH_RE.search(item.get("filename", ""))
    ]
    return {
        "changed_files_count": len(names),
        "test_files_count": len(test_files),
        "has_test_signal": bool(test_files),
        "test_files_sample": test_files[:20],
        "changed_files_sample": names[:30],
    }


def extract_links(text: str, source_repo: str, ecosystem_full_names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ecosystem_links: list[dict[str, Any]] = []
    external_links: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in GITHUB_LINK_RE.finditer(text):
        full_name = f"{match.group('owner')}/{match.group('repo')}"
        number = int(match.group("number"))
        key = (full_name.lower(), number)
        if key in seen or full_name.lower() == source_repo.lower():
            continue
        seen.add(key)
        row = {
            "full_name": full_name,
            "number": number,
            "link_kind": match.group("link_kind") or "unknown",
            "url": f"https://github.com/{full_name}/pull/{number}" if match.group("link_kind") == "pull" else f"https://github.com/{full_name}/issues/{number}",
        }
        if full_name.lower() in ecosystem_full_names:
            ecosystem_links.append(row)
        else:
            external_links.append(row)
    return ecosystem_links, external_links


def strict_rejection_reasons(bundle: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    reasons.extend(bundle.get("rejection_reasons") or [])

    involved = bundle.get("ecosystem_involved_prs") or bundle.get("involved_prs") or {}
    ios_hits: list[str] = []
    for full_name, item in involved.items():
        if item.get("metadata_error"):
            reasons.append(f"{full_name}#{item.get('pr_number')}: PR metadata unavailable ({item.get('metadata_error')})")
        elif not item.get("metadata", {}).get("merged_at"):
            reasons.append(f"{full_name}#{item.get('pr_number')}: ecosystem-involved PR is not merged")
        if not item.get("has_test_signal"):
            reasons.append(f"{full_name}#{item.get('pr_number')}: ecosystem-involved PR has no test-file signal")
        ios_hits.extend(ios_platform_hits(full_name, item))

    for title in [bundle.get("source_title") or ""]:
        if title and IOS_TITLE_RE.search(title):
            ios_hits.append(f"title:{title}")

    if ios_hits:
        bundle["ios_platform_hits"] = sorted(set(ios_hits))
        reasons.append("case touches Apple iOS platform code/tests, which is not supported by the Linux benchmark environment")

    seen: set[str] = set()
    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in seen:
            seen.add(reason)
            deduped.append(reason)
    return deduped


def ios_platform_hits(full_name: str, item: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    if IOS_REPO_RE.search(full_name):
        hits.append(f"repo:{full_name}")
    metadata = item.get("metadata") or {}
    title = metadata.get("title") or ""
    if title and IOS_TITLE_RE.search(title):
        hits.append(f"title:{full_name}#{item.get('pr_number')}:{title}")
    for field in ("changed_files_sample", "test_files_sample"):
        for path in item.get(field) or []:
            if IOS_PATH_RE.search(path):
                hits.append(f"path:{full_name}:{path}")
    return hits


def classify_bundle(bundle: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    reasons = strict_rejection_reasons(bundle)
    bundle["strict_filter_rejection_reasons"] = reasons
    bundle["strict_status"] = "rejected" if reasons else "candidate"
    return bundle, reasons


def selected_ecosystems(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    filtered = rows
    if args.ecosystem:
        wanted = {item.lower() for item in args.ecosystem}
        filtered = [row for row in filtered if row["org"].lower() in wanted]
    if args.max_ecosystems:
        filtered = filtered[: args.max_ecosystems]
    return filtered


def reset_output_dir(output_dir: Path) -> None:
    incremental_files = [
        output_dir / "candidates.jsonl",
        output_dir / "rejected.jsonl",
        output_dir / "errors.jsonl",
        output_dir / "processed_prs.jsonl",
    ]
    for path in incremental_files:
        path.unlink(missing_ok=True)


def write_report(output_dir: Path, args: argparse.Namespace, stats: dict[str, Any]) -> None:
    report_lines = [
        "# PR Mining Candidates",
        "",
        f"Ecosystem: `{stats['ecosystem']}`",
        f"Input ecosystems: `{args.ecosystems}`",
        "",
        "## Rules",
        "",
        f"- The GitHub organization is enumerated at run time; active non-archived, non-fork public repositories are mined by default.",
        f"- Closed PRs are scanned with `merged_at >= {args.since}`. `--prs-per-repo 0` means all PRs in that date range.",
        "- A candidate must contain at least one explicit cross-repo GitHub PR/issue link to another repository inside the same ecosystem repository set.",
        "- External PR/issue/proposal links are allowed and retained as evidence for manual review; they do not automatically reject a bundle.",
        "- For each source PR with ecosystem links, the source PR and ecosystem-linked PRs form one `candidates.jsonl` record.",
        "- The source item and every ecosystem-linked item must resolve to a merged GitHub PR via the pulls API.",
        "- Every ecosystem-involved PR must have a changed test-file signal; docs/spec/context PRs are not test-exempt in the strict main pipeline.",
        "- `pr_timeline` records created/updated/closed/merged timestamps for each ecosystem-involved PR.",
        "- Shared tickets, proposal IDs, dependency phrases, and semantic similarity are intentionally ignored in this strict pass.",
        "",
        "## Counts",
        "",
        f"- Active repositories enumerated: {stats['repos_enumerated']}",
        f"- Repositories scanned: {stats['repos_scanned']}",
        f"- Scanned merged PRs: {stats['scanned_prs']}",
        f"- Skipped processed PRs: {stats['skipped_processed_prs']}",
        f"- Source PRs with ecosystem cross-repo links: {stats['linked_pr_records']}",
        f"- Candidate source-PR bundles: {stats['candidate_bundles']}",
        f"- Rejected source-PR bundles: {stats['rejected_bundles']}",
        f"- Errors: {stats['errors']}",
        "",
        "## Output Files",
        "",
        "- `candidates.jsonl`: final candidate bundles. Every ecosystem-involved PR has a test-file signal.",
        "- `rejected.jsonl`: linked cross-repo PR bundles rejected by merge, metadata, or strict test-signal rules.",
        "- `errors.jsonl`: API errors.",
        "- `processed_prs.jsonl`: resume log for each scanned source PR.",
        "",
        "## Resume",
        "",
        "Use `--resume` to keep existing outputs and skip source PRs already recorded in `processed_prs.jsonl`.",
    ]
    (output_dir / "README.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def repo_inventory_path(output_dir: Path) -> Path:
    return output_dir / "repository_inventory.json"


def load_or_collect_repos(eco: dict[str, Any], args: argparse.Namespace, output_dir: Path) -> tuple[list[RepoRef], set[str], int]:
    org = eco["org"]
    inventory = repo_inventory_path(output_dir)
    if args.resume and inventory.exists() and not args.refresh_repos:
        data = json.loads(inventory.read_text(encoding="utf-8"))
        repo_names = [item["name"] for item in data.get("repositories", [])]
        enumerated_count = len(repo_names)
    else:
        print(f"[repos] {org} enumerating organization repositories", file=sys.stderr, flush=True)
        repos = list_org_repositories(
            org,
            args.sleep,
            args.retries,
            include_archived=args.include_archived,
            include_forks=args.include_forks,
            max_pages=args.max_repo_pages,
        )
        repo_names = sorted({item["name"] for item in repos if item.get("name")})
        enumerated_count = len(repo_names)
        write_json(
            inventory,
            {
                "ecosystem": org,
                "source": "GitHub owner repos API",
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "include_archived": args.include_archived,
                "include_forks": args.include_forks,
                "repositories": [
                    {
                        "name": item.get("name"),
                        "full_name": item.get("full_name"),
                        "archived": item.get("archived"),
                        "fork": item.get("fork"),
                        "disabled": item.get("disabled"),
                        "pushed_at": item.get("pushed_at"),
                        "updated_at": item.get("updated_at"),
                        "html_url": item.get("html_url"),
                    }
                    for item in repos
                ],
            },
        )
    ecosystem_full_names = {f"{org}/{name}".lower() for name in repo_names}

    scan_repo_names = repo_names
    if args.repo:
        wanted = {item.lower() for item in args.repo}
        scan_repo_names = [name for name in scan_repo_names if name.lower() in wanted]
    if args.max_repos_per_ecosystem:
        scan_repo_names = scan_repo_names[: args.max_repos_per_ecosystem]
    return [RepoRef(org, org, repo) for repo in scan_repo_names], ecosystem_full_names, enumerated_count


def mine_ecosystem(eco: dict[str, Any], args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    org = eco["org"]
    output_dir.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        reset_output_dir(output_dir)
    else:
        (output_dir / "errors.jsonl").unlink(missing_ok=True)

    processed_keys: set[str] = {
        row["source_key"]
        for row in read_jsonl(output_dir / "processed_prs.jsonl")
        if row.get("source_key")
    }
    candidate_bundles = read_jsonl(output_dir / "candidates.jsonl") if args.resume else []
    rejected_bundle_count = len(read_jsonl(output_dir / "rejected.jsonl")) if args.resume else 0
    errors: list[dict[str, Any]] = []

    scanned_pr_count = 0
    skipped_processed_count = 0
    linked_pr_count = 0

    repos, ecosystem_full_names, repos_enumerated = load_or_collect_repos(eco, args, output_dir)
    since_dt = parse_since(args.since)

    try:
        for repo in repos:
            print(f"[mine] {repo.full_name}", file=sys.stderr, flush=True)
            try:
                prs = iter_merged_prs_since(
                    repo,
                    since_dt,
                    args.prs_per_repo,
                    args.sleep,
                    args.retries,
                    args.max_pr_pages,
                )
            except (HTTPError, URLError, RuntimeError) as exc:
                error = {"ecosystem": org, "repo": repo.full_name, "stage": "search_prs", "error": str(exc)}
                errors.append(error)
                append_jsonl(output_dir / "errors.jsonl", error)
                continue

            for pr in prs:
                scanned_pr_count += 1
                source_key = f"{repo.full_name}#{int(pr['number'])}"
                if source_key in processed_keys:
                    skipped_processed_count += 1
                    continue
                title = pr.get("title") or ""
                body = pr.get("body") or ""
                text = f"{title}\n{body}"
                ecosystem_links, external_links = extract_links(text, repo.full_name, ecosystem_full_names)

                if not ecosystem_links:
                    append_jsonl(
                        output_dir / "processed_prs.jsonl",
                        {
                            "ecosystem": org,
                            "source_key": source_key,
                            "source_repo": repo.full_name,
                            "source_pr": int(pr["number"]),
                            "source_created_at": pr.get("created_at"),
                            "source_updated_at": pr.get("updated_at"),
                            "source_closed_at": pr.get("closed_at"),
                            "source_merged_at": pr.get("merged_at"),
                            "status": "no_ecosystem_cross_repo_links",
                            "external_links": external_links,
                        },
                    )
                    processed_keys.add(source_key)
                    continue

                linked_pr_count += 1

                source_metadata, source_metadata_error = fetch_pr_metadata(repo.full_name, int(pr["number"]), args.sleep, args.retries)
                source_files, source_file_error = fetch_pr_files(repo.full_name, int(pr["number"]), args.sleep, args.retries)
                source_summary = summarize_files(source_files)
                transient_errors: list[dict[str, Any]] = []
                rejection_reasons: list[str] = []
                if is_transient_api_error(source_metadata_error):
                    transient_errors.append(
                        {
                            "ecosystem": org,
                            "source_key": source_key,
                            "repo": repo.full_name,
                            "pr": int(pr["number"]),
                            "stage": "source_pr_metadata",
                            "error": source_metadata_error,
                        }
                    )
                elif source_metadata_error:
                    rejection_reasons.append(f"{repo.full_name}#{int(pr['number'])}: source PR metadata unavailable ({source_metadata_error})")
                elif not source_metadata or not source_metadata.get("merged_at"):
                    rejection_reasons.append(f"{repo.full_name}#{int(pr['number'])}: source PR is not merged")
                if is_transient_file_error(source_file_error):
                    transient_errors.append(
                        {
                            "ecosystem": org,
                            "source_key": source_key,
                            "repo": repo.full_name,
                            "pr": int(pr["number"]),
                            "stage": "source_pr_files",
                            "error": source_file_error,
                        }
                    )
                bundle_involved = {
                    repo.full_name: {
                        "pr_number": int(pr["number"]),
                        "url": pr.get("html_url"),
                        "role": "source",
                        "metadata": summarize_pr_metadata(source_metadata),
                        "metadata_error": source_metadata_error,
                        "has_test_signal": source_summary["has_test_signal"],
                        "test_files_count": source_summary["test_files_count"],
                        "test_files_sample": source_summary["test_files_sample"],
                        "changed_files_count": source_summary["changed_files_count"],
                        "changed_files_sample": source_summary["changed_files_sample"],
                        "file_error": source_file_error,
                    }
                }
                pr_timeline = [
                    pr_timeline_item(repo.full_name, int(pr["number"]), "source", summarize_pr_metadata(source_metadata))
                ]

                for link in ecosystem_links:
                    linked_metadata, linked_metadata_error = fetch_pr_metadata(
                        link["full_name"], link["number"], args.sleep, args.retries
                    )
                    if is_transient_api_error(linked_metadata_error):
                        transient_errors.append(
                            {
                                "ecosystem": org,
                                "source_key": source_key,
                                "repo": link["full_name"],
                                "pr": link["number"],
                                "stage": "linked_pr_metadata",
                                "error": linked_metadata_error,
                            }
                        )
                    elif linked_metadata_error:
                        if link.get("link_kind") == "pull" or linked_metadata_error != "HTTP 404":
                            rejection_reasons.append(
                                f"{link['full_name']}#{link['number']}: linked item is not an available PR ({linked_metadata_error})"
                            )
                    elif not linked_metadata or not linked_metadata.get("merged_at"):
                        rejection_reasons.append(f"{link['full_name']}#{link['number']}: linked PR is not merged")

                    linked_summary: dict[str, Any] = {
                        "changed_files_count": None,
                        "test_files_count": None,
                        "has_test_signal": False,
                        "test_files_sample": [],
                        "changed_files_sample": [],
                    }
                    linked_file_error = ""
                    linked_files, linked_file_error = fetch_pr_files(link["full_name"], link["number"], args.sleep, args.retries)
                    if linked_files:
                        linked_summary = summarize_files(linked_files)
                    if is_transient_file_error(linked_file_error):
                        transient_errors.append(
                            {
                                "ecosystem": org,
                                "source_key": source_key,
                                "repo": link["full_name"],
                                "pr": link["number"],
                                "stage": "linked_pr_files",
                                "error": linked_file_error,
                            }
                        )

                    bundle_involved[link["full_name"]] = {
                        "pr_number": link["number"],
                        "url": link["url"],
                        "role": "linked",
                        "metadata": summarize_pr_metadata(linked_metadata),
                        "metadata_error": linked_metadata_error,
                        "has_test_signal": linked_summary["has_test_signal"],
                        "test_files_count": linked_summary["test_files_count"],
                        "test_files_sample": linked_summary["test_files_sample"],
                        "changed_files_count": linked_summary["changed_files_count"],
                        "changed_files_sample": linked_summary["changed_files_sample"],
                        "file_error": linked_file_error,
                    }
                    pr_timeline.append(
                        pr_timeline_item(
                            link["full_name"],
                            link["number"],
                            "linked",
                            summarize_pr_metadata(linked_metadata),
                        )
                    )

                if transient_errors:
                    for error in transient_errors:
                        errors.append(error)
                        append_jsonl(output_dir / "errors.jsonl", error)
                    print(
                        f"[transient-error] {org} {source_key} retry_later={len(transient_errors)}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                bundle = {
                    "ecosystem": org,
                    "source_repo": repo.full_name,
                    "source_pr": int(pr["number"]),
                    "source_url": pr.get("html_url"),
                    "source_title": title,
                    "linked_prs": ecosystem_links,
                    "external_links": external_links,
                    "repos": sorted(bundle_involved.keys()),
                    "repo_count": len(bundle_involved),
                    "all_involved_prs_have_test_signal": all(item["has_test_signal"] for item in bundle_involved.values()),
                    "any_involved_pr_has_test_signal": any(item["has_test_signal"] for item in bundle_involved.values()),
                    "all_involved_items_are_merged_prs": not any(
                        item.get("metadata_error") or not item.get("metadata", {}).get("merged_at")
                        for item in bundle_involved.values()
                    ),
                    "rejection_reasons": rejection_reasons,
                    "ecosystem_involved_prs": bundle_involved,
                    # Backward-compatible alias for older review scripts/results.
                    "involved_prs": bundle_involved,
                    "pr_timeline": pr_timeline,
                    "manual_review_required": (
                        "Verify ecosystem-linked PRs implement the same demand, inspect external_links as supporting "
                        "evidence rather than exclusion, and run impact closure for ecosystem repos not in this bundle."
                    ),
                }
                bundle, strict_reasons = classify_bundle(bundle)
                if not strict_reasons:
                    candidate_bundles.append(bundle)
                    append_jsonl(output_dir / "candidates.jsonl", bundle)
                    outcome = "candidate"
                else:
                    rejected_bundle_count += 1
                    append_jsonl(output_dir / "rejected.jsonl", bundle)
                    outcome = "rejected"
                append_jsonl(
                    output_dir / "processed_prs.jsonl",
                        {
                            "ecosystem": org,
                            "source_key": source_key,
                            "source_repo": repo.full_name,
                            "source_pr": int(pr["number"]),
                            "source_created_at": pr.get("created_at"),
                            "source_updated_at": pr.get("updated_at"),
                            "source_closed_at": pr.get("closed_at"),
                            "source_merged_at": pr.get("merged_at"),
                            "status": outcome,
                            "repo_count": len(bundle["repos"]),
                        "all_involved_prs_have_test_signal": bundle["all_involved_prs_have_test_signal"],
                    },
                )
                processed_keys.add(source_key)
                print(
                    f"[{outcome}] {org} {repo.full_name}#{pr['number']} "
                    f"repos={len(bundle['repos'])} any_tests={bundle['any_involved_pr_has_test_signal']} "
                    f"all_tests={bundle['all_involved_prs_have_test_signal']}",
                    file=sys.stderr,
                    flush=True,
                )
                if args.stop_after_candidates and len(candidate_bundles) >= args.stop_after_candidates:
                    break
            if args.stop_after_candidates and len(candidate_bundles) >= args.stop_after_candidates:
                break
    except Exception as exc:  # noqa: BLE001 - isolate ecosystem-level failures for batch runs.
        error = {"ecosystem": org, "stage": "ecosystem", "error": str(exc)}
        errors.append(error)
        append_jsonl(output_dir / "errors.jsonl", error)

    stats = {
        "ecosystem": org,
        "output_dir": str(output_dir),
        "repos_enumerated": repos_enumerated,
        "repos_scanned": len(repos),
        "scanned_prs": scanned_pr_count,
        "skipped_processed_prs": skipped_processed_count,
        "linked_pr_records": linked_pr_count,
        "candidate_bundles": len(candidate_bundles),
        "rejected_bundles": rejected_bundle_count,
        "errors": len(errors),
    }
    write_report(output_dir, args, stats)
    return stats


def mining_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": "ecosyncbench.pr_mining.all_repos_since.v1",
        "ecosystems": str(args.ecosystems),
        "ecosystem_filter": sorted(args.ecosystem or []),
        "max_ecosystems": args.max_ecosystems,
        "repo_filter": sorted(args.repo or []),
        "since": args.since,
        "prs_per_repo": args.prs_per_repo,
        "include_archived": args.include_archived,
        "include_forks": args.include_forks,
        "max_repos_per_ecosystem": args.max_repos_per_ecosystem,
        "max_repo_pages": args.max_repo_pages,
        "max_pr_pages": args.max_pr_pages,
    }


def ensure_resume_config(args: argparse.Namespace) -> None:
    path = args.output_dir / "mining_config.json"
    current = mining_config(args)
    if args.resume and path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous != current:
            raise SystemExit(
                "resume config mismatch; use a different --output-dir or rerun without --resume\n"
                f"previous={json.dumps(previous, ensure_ascii=False, sort_keys=True)}\n"
                f"current={json.dumps(current, ensure_ascii=False, sort_keys=True)}"
            )
    write_json(path, current)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecosystems", type=Path, default=DEFAULT_ECOSYSTEMS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--ecosystem", action="append", help="Limit to one ecosystem; may be repeated.")
    parser.add_argument("--max-ecosystems", type=int, default=0)
    parser.add_argument("--repo", action="append", help="Debug only: limit scanned repositories by repo name; may be repeated.")
    parser.add_argument("--max-repos-per-ecosystem", type=int, default=0, help="Debug only. Default 0 scans all enumerated repos.")
    parser.add_argument("--since", default="2024-01-01", help="Keep merged PRs with merged_at on or after this date.")
    parser.add_argument("--prs-per-repo", type=int, default=0, help="Maximum merged PRs per repo after --since. Default 0 means all.")
    parser.add_argument("--max-repo-pages", type=int, default=0, help="Debug safety cap for repo pages of 100 repos. Default 0 means all.")
    parser.add_argument("--max-pr-pages", type=int, default=0, help="Debug safety cap for Pulls API pages of 100 PRs per repo. Default 0 means all since --since.")
    parser.add_argument("--include-archived", action="store_true", help="Include archived repos when enumerating organization repositories.")
    parser.add_argument("--include-forks", action="store_true", help="Include fork repos when enumerating organization repositories.")
    parser.add_argument("--refresh-repos", action="store_true", help="Refresh repository_inventory.json even when --resume is used.")
    parser.add_argument("--stop-after-candidates", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="Resume in an existing output directory without clearing files.")
    parser.add_argument("--workers", type=int, default=8, help="Number of ecosystems to mine concurrently. Use 1 for sequential runs.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Optional delay after each GitHub request. Default 0 disables per-request sleeping.")
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    ecosystems = selected_ecosystems(read_ecosystems(args.ecosystems), args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ensure_resume_config(args)

    batch_summary_path = args.output_dir / "batch_summary.jsonl"
    batch_summary_path.unlink(missing_ok=True)

    all_stats: list[dict[str, Any]] = []
    stats_by_ecosystem: dict[str, dict[str, Any]] = {}

    def record_stats(stats: dict[str, Any]) -> None:
        all_stats.append(stats)
        stats_by_ecosystem[stats["ecosystem"]] = stats
        append_jsonl(batch_summary_path, stats)
        print(
            f"[ecosystem-done] {stats['ecosystem']} candidates={stats['candidate_bundles']} "
            f"rejected={stats['rejected_bundles']} errors={stats['errors']} out={stats['output_dir']}",
            file=sys.stderr,
            flush=True,
        )

    print(
        f"[batch-start] ecosystems={len(ecosystems)} workers={args.workers} sleep={args.sleep}",
        file=sys.stderr,
        flush=True,
    )
    if args.workers == 1 or len(ecosystems) <= 1:
        for eco in ecosystems:
            eco_output_dir = args.output_dir / eco["org"]
            record_stats(mine_ecosystem(eco, args, eco_output_dir))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(mine_ecosystem, eco, args, args.output_dir / eco["org"]): eco
                for eco in ecosystems
            }
            for future in as_completed(futures):
                eco = futures[future]
                try:
                    record_stats(future.result())
                except Exception as exc:  # noqa: BLE001 - keep long batch runs moving.
                    error_stats = {
                        "ecosystem": eco["org"],
                        "output_dir": str(args.output_dir / eco["org"]),
                        "repos_enumerated": 0,
                        "repos_scanned": 0,
                        "scanned_prs": 0,
                        "skipped_processed_prs": 0,
                        "linked_pr_records": 0,
                        "candidate_bundles": 0,
                        "rejected_bundles": 0,
                        "errors": 1,
                        "fatal_error": str(exc),
                    }
                    append_jsonl(args.output_dir / eco["org"] / "errors.jsonl", error_stats)
                    record_stats(error_stats)

    summary_lines = [
        "# PR Mining Batch Summary",
        "",
        f"Input ecosystems: `{args.ecosystems}`",
        f"Selected ecosystems: {len(ecosystems)}",
        f"Workers: {args.workers}",
        f"Sleep seconds: {args.sleep}",
        "",
        "| Ecosystem | Repos | Scanned PRs | Skipped | Linked PRs | Candidates | Rejected | Errors | Output |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for eco in ecosystems:
        stats = stats_by_ecosystem.get(eco["org"])
        if not stats:
            continue
        summary_lines.append(
            f"| `{stats['ecosystem']}` | {stats['repos_scanned']} | {stats['scanned_prs']} | {stats['skipped_processed_prs']} | "
            f"{stats['linked_pr_records']} | {stats['candidate_bundles']} | {stats['rejected_bundles']} | "
            f"{stats['errors']} | `{stats['output_dir']}` |"
        )
    (args.output_dir / "batch_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print(f"ecosystems: {len(ecosystems)}")
    print(f"candidate bundles: {sum(item['candidate_bundles'] for item in all_stats)}")
    print(f"rejected bundles: {sum(item['rejected_bundles'] for item in all_stats)}")
    print(f"errors: {sum(item['errors'] for item in all_stats)}")
    print(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
