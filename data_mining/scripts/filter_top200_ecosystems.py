#!/usr/bin/env python3
"""Run the first two objective ecosystem filters.

This script performs live collection only:

1. Use GitStar organization pages to collect TopN organizations, their
   repository counts, and their Top10 repositories.
2. Use GitHub repo API only for those Top10 repositories to check archived and
   pushed_at.

No manual-audit logic is encoded here.
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data_mining" / "data" / "ecosystems"
GITSTAR = "https://gitstar-ranking.com"
UTC = timezone.utc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def fetch_text(url: str, retries: int, sleep_seconds: float) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": "WIDESWE-data-mining"})
            with urlopen(req, timeout=45) as resp:
                if sleep_seconds:
                    time.sleep(sleep_seconds)
                return resp.read().decode("utf-8", errors="replace")
        except (URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(30, 3 * (2**attempt)))
                continue
            raise
    raise RuntimeError(f"failed to fetch {url}") from last_exc


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


def github_get_json(url: str, retries: int, sleep_seconds: float) -> Any:
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
        except (URLError, http.client.RemoteDisconnected) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(30, 3 * (2**attempt)))
                continue
            raise
    raise RuntimeError(f"GitHub request failed: {url}") from last_exc


def parse_int(text: str) -> int:
    return int(re.sub(r"\D+", "", text))


def parse_ranking_page(content: str) -> list[dict[str, Any]]:
    items = re.findall(r'<a class="list-group-item paginated_item" href="/([^"/]+)">([\s\S]*?)</a>', content)
    rows: list[dict[str, Any]] = []
    for org, block in items:
        rank_match = re.search(r"<span class='name'>\s*(\d+)\.", block)
        star_match = re.search(r"stargazers_count pull-right[\s\S]*?</i>\s*([0-9,]+)", block)
        if rank_match and star_match:
            rows.append(
                {
                    "rank": int(rank_match.group(1)),
                    "org": html.unescape(org),
                    "gitstar_total_stars": parse_int(star_match.group(1)),
                    "gitstar_url": f"{GITSTAR}/{html.unescape(org)}",
                    "github_url": f"https://github.com/{html.unescape(org)}",
                }
            )
    return rows


def parse_org_page(org: str, content: str, top_k: int) -> tuple[int | None, list[dict[str, Any]]]:
    count_match = re.search(r"<h3>\s*([0-9,]+)\s+Repositories\s*</h3>", content)
    repo_count = parse_int(count_match.group(1)) if count_match else None
    items = re.findall(r'<a class="list-group-item paginated_full_item" href="/([^"/]+)/([^"]+)">([\s\S]*?)</a>', content)
    repos: list[dict[str, Any]] = []
    for owner, repo, block in items:
        if owner.lower() != org.lower():
            continue
        star_match = re.search(r"stargazers_count pull-right[\s\S]*?</i>\s*([0-9,]+)", block)
        repo_name = html.unescape(repo.strip())
        repos.append(
            {
                "name": repo_name,
                "full_name": f"{org}/{repo_name}",
                "github_url": f"https://github.com/{org}/{repo_name}",
                "gitstar_url": f"{GITSTAR}/{org}/{repo_name}",
                "gitstar_stars": parse_int(star_match.group(1)) if star_match else None,
            }
        )
        if len(repos) >= top_k:
            break
    return repo_count, repos


def collect_gitstar(top_n: int, top_k: int, retries: int, sleep_seconds: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages = (top_n + 99) // 100
    ranked: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        print(f"[gitstar] organizations page {page}", file=sys.stderr, flush=True)
        content = fetch_text(f"{GITSTAR}/organizations?page={page}", retries, sleep_seconds)
        ranked.extend(parse_ranking_page(content))

    rows: list[dict[str, Any]] = []
    for seed in sorted(ranked, key=lambda row: row["rank"])[:top_n]:
        org = seed["org"]
        print(f"[gitstar] {org}", file=sys.stderr, flush=True)
        try:
            content = fetch_text(f"{GITSTAR}/{quote(org)}", retries, sleep_seconds)
            repo_count, top_repos = parse_org_page(org, content, top_k)
            seed["gitstar_repository_count"] = repo_count
            seed["top10_repos"] = top_repos
        except Exception as exc:  # noqa: BLE001 - collect and report partial failures.
            seed["gitstar_repository_count"] = None
            seed["top10_repos"] = []
            seed["collection_error"] = str(exc)
            errors.append({"org": org, "stage": "gitstar_org_page", "error": str(exc)})
        rows.append(seed)
    return rows, errors


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def collect_top10_github_status(ecosystems: list[dict[str, Any]], retries: int, sleep_seconds: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repo_status: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for eco in ecosystems:
        org = eco["org"]
        for repo in eco.get("top10_repos", []):
            full_name = repo["full_name"]
            print(f"[github] {full_name}", file=sys.stderr, flush=True)
            try:
                data = github_get_json(f"https://api.github.com/repos/{quote(full_name, safe='/')}", retries, sleep_seconds)
                repo_status.append(
                    {
                        "org": org,
                        "repo": repo["name"],
                        "full_name": full_name,
                        "archived": data.get("archived"),
                        "disabled": data.get("disabled"),
                        "fork": data.get("fork"),
                        "pushed_at": data.get("pushed_at"),
                        "updated_at": data.get("updated_at"),
                        "default_branch": data.get("default_branch"),
                        "language": data.get("language"),
                        "html_url": data.get("html_url"),
                    }
                )
            except (HTTPError, URLError, RuntimeError, http.client.RemoteDisconnected) as exc:
                errors.append({"org": org, "repo": full_name, "stage": "github_repo", "error": str(exc)})
                repo_status.append({"org": org, "repo": repo["name"], "full_name": full_name, "error": str(exc)})
    return repo_status, errors


def filter_ecosystems(
    ecosystems: list[dict[str, Any]],
    repo_status: list[dict[str, Any]],
    inactive_months: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    repo_count_keep: list[dict[str, Any]] = []
    repo_count_drop: list[dict[str, Any]] = []
    for eco in ecosystems:
        count = eco.get("gitstar_repository_count")
        if isinstance(count, int) and count >= 2:
            repo_count_keep.append(eco)
        else:
            item = dict(eco)
            item["excluded_reason"] = f"GitStar repository_count={count}, below 2."
            repo_count_drop.append(item)

    status_by_org: dict[str, list[dict[str, Any]]] = {}
    for status in repo_status:
        status_by_org.setdefault(status["org"], []).append(status)

    cutoff = datetime.now(UTC) - timedelta(days=inactive_months * 30)
    active_keep: list[dict[str, Any]] = []
    active_drop: list[dict[str, Any]] = []
    for eco in repo_count_keep:
        statuses = status_by_org.get(eco["org"], [])
        good_statuses = [row for row in statuses if not row.get("error")]
        non_archived = [row for row in good_statuses if not row.get("archived")]
        pushed_dates = [parse_dt(row.get("pushed_at")) for row in non_archived]
        pushed_dates = [date for date in pushed_dates if date is not None]
        latest = max(pushed_dates) if pushed_dates else None
        if len(non_archived) < 2:
            item = dict(eco)
            item["excluded_reason"] = f"Top10 non-archived GitHub repositories={len(non_archived)}, below 2."
            active_drop.append(item)
        elif latest is None or latest < cutoff:
            item = dict(eco)
            item["excluded_reason"] = (
                f"Latest Top10 pushed_at={latest.isoformat() if latest else 'missing'}, "
                f"older than the {inactive_months}-month activity window."
            )
            active_drop.append(item)
        else:
            item = dict(eco)
            item["top10_non_archived_count"] = len(non_archived)
            item["top10_latest_pushed_at"] = latest.isoformat()
            active_keep.append(item)
    return repo_count_keep, repo_count_drop, active_keep, active_drop


def repo_names(eco: dict[str, Any]) -> list[str]:
    return [repo["name"] for repo in eco.get("top10_repos", [])][:10]


def write_screening_report(
    path: Path,
    collected_at: str,
    seed_count: int,
    repo_count_keep: list[dict[str, Any]],
    repo_count_drop: list[dict[str, Any]],
    active_keep: list[dict[str, Any]],
    active_drop: list[dict[str, Any]],
    inactive_months: int,
) -> None:
    lines = [
        "# Top200 Ecosystem Screening Report",
        "",
        f"collected_at: `{collected_at}`",
        "",
        "## Rules",
        "",
        "1. Read `N Repositories` from each GitStar organization page and reject counts below 2.",
        f"2. Inspect `archived` and `pushed_at` for each ecosystem's GitStar Top10; reject fewer than 2 non-archived repositories or no push within {inactive_months} months.",
        "",
        "## Outputs",
        "",
        "- `gitstar_top200_snapshot.json`",
        "- `github_top10_repo_status.jsonl`",
        "- `screening_stage2_ecosystems.json`",
        "- `screening_report.md`",
        "",
        "## Counts",
        "",
        f"- Top200 seed: {seed_count}",
        f"- After repository-count filter: kept {len(repo_count_keep)}, rejected {len(repo_count_drop)}",
        f"- After archived/inactive filter: kept {len(active_keep)}, rejected {len(active_drop)}",
        "",
        "## Ecosystems Retained After Both Filters",
        "",
    ]
    for eco in active_keep:
        lines.append(f"- {eco['rank']}. `{eco['org']}` - top10: {', '.join(repo_names(eco))}")
    lines.extend(["", "## Rejected By Repository Count", ""])
    for eco in repo_count_drop:
        lines.append(f"- {eco['rank']}. `{eco['org']}` - {eco['excluded_reason']}")
    lines.extend(["", "## Rejected As Archived Or Inactive", ""])
    for eco in active_drop:
        lines.append(f"- {eco['rank']}. `{eco['org']}` - {eco['excluded_reason']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--inactive-months", type=int, default=24)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    collected_at = datetime.now(UTC).isoformat()

    ecosystems, gitstar_errors = collect_gitstar(args.top_n, args.top_k, args.retries, args.sleep)
    repo_status, github_errors = collect_top10_github_status(ecosystems, args.retries, args.sleep)
    repo_count_keep, repo_count_drop, active_keep, active_drop = filter_ecosystems(ecosystems, repo_status, args.inactive_months)

    write_json(args.output_dir / "gitstar_top200_snapshot.json", ecosystems)
    write_jsonl(args.output_dir / "github_top10_repo_status.jsonl", repo_status)
    write_jsonl(args.output_dir / "collection_errors.jsonl", [*gitstar_errors, *github_errors])
    write_json(args.output_dir / "screening_stage2_ecosystems.json", active_keep)
    write_screening_report(
        args.output_dir / "screening_report.md",
        collected_at,
        len(ecosystems),
        repo_count_keep,
        repo_count_drop,
        active_keep,
        active_drop,
        args.inactive_months,
    )

    print(f"Top{len(ecosystems)} seed: {len(ecosystems)}")
    print(f"repo-count keep={len(repo_count_keep)} drop={len(repo_count_drop)}")
    print(f"active keep={len(active_keep)} drop={len(active_drop)}")
    print(args.output_dir / "screening_stage2_ecosystems.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
