"""Remote GitHub Pull Request inspection tools (via `gh` CLI or GitHub REST API)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Callable

import httpx

_GH_REMOTE_RE = re.compile(
    r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)


def _find_gh_binary() -> str | None:
    for candidate in (
        shutil.which("gh"),
        "/home/linuxbrew/.linuxbrew/bin/gh",
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "/usr/bin/gh",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _detect_github_repo(cwd: str) -> str | None:
    res = subprocess.run(
        ["git", "-C", cwd, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    m = _GH_REMOTE_RE.search(res.stdout.strip())
    if m:
        return f"{m.group('owner')}/{m.group('repo')}"
    return None


def _gh_headers() -> dict[str, str]:
    headers = {
        "User-Agent": "samambaia-agent",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def make_github_pr_tools(cwd: str) -> list[Callable[..., Any]]:
    def gh_pr_view(pr_number: int, repo: str = "") -> str:
        """Fetches title, author, state, branches, and description of a remote GitHub Pull Request.

        Args:
            pr_number: Pull Request number (e.g. 42).
            repo: Optional 'owner/repo' (auto-detected from git remote origin when omitted).
        """
        target_repo = repo.strip() or _detect_github_repo(cwd)
        gh_bin = _find_gh_binary()
        if gh_bin:
            cmd = [gh_bin, "pr", "view", str(pr_number)]
            if target_repo:
                cmd.extend(["--repo", target_repo])
            res = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=20, check=False
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()

        if not target_repo:
            return "Could not auto-detect GitHub repository (pass repo='owner/name')."

        url = f"https://api.github.com/repos/{target_repo}/pulls/{int(pr_number)}"
        try:
            resp = httpx.get(url, headers=_gh_headers(), timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
            return (
                f"PR #{data.get('number')}: {data.get('title')}\n"
                f"Author: {data.get('user', {}).get('login')} | State: {data.get('state')}\n"
                f"Branches: {data.get('head', {}).get('ref')} -> {data.get('base', {}).get('ref')}\n"
                f"Changed files: {data.get('changed_files')} (+{data.get('additions')} / -{data.get('deletions')})\n\n"
                f"Description:\n{data.get('body') or '(no description)'}"
            )
        except Exception as exc:
            return f"Error querying PR #{pr_number} on {target_repo}: {exc}"

    def gh_pr_diff(pr_number: int, repo: str = "") -> str:
        """Downloads the unified diff of a remote GitHub Pull Request without changing local git branches.

        Args:
            pr_number: Pull Request number (e.g. 42).
            repo: Optional 'owner/repo' (auto-detected from git remote origin when omitted).
        """
        target_repo = repo.strip() or _detect_github_repo(cwd)
        gh_bin = _find_gh_binary()
        diff_text = ""

        if gh_bin:
            cmd = [gh_bin, "pr", "diff", str(pr_number)]
            if target_repo:
                cmd.extend(["--repo", target_repo])
            res = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=25, check=False
            )
            if res.returncode == 0 and res.stdout:
                diff_text = res.stdout

        if not diff_text:
            if not target_repo:
                return "Could not auto-detect GitHub repository (pass repo='owner/name')."
            headers = _gh_headers()
            headers["Accept"] = "application/vnd.github.v3.diff"
            url = f"https://api.github.com/repos/{target_repo}/pulls/{int(pr_number)}"
            try:
                resp = httpx.get(url, headers=headers, timeout=20.0)
                resp.raise_for_status()
                diff_text = resp.text
            except Exception as exc:
                return f"Error downloading diff for PR #{pr_number} on {target_repo}: {exc}"

        if len(diff_text) > 26000:
            return (
                diff_text[:26000]
                + "\n\n[... PR diff truncated at 26k chars ...]"
            )
        return diff_text or "(Empty PR diff)"

    return [gh_pr_view, gh_pr_diff]
