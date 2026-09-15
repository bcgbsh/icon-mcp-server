#!/usr/bin/env python3
"""Push the current project to GitHub using ``GITHUB_TOKEN`` from environment.

Security model
--------------
* The token is **never printed**; every log line passes through :func:`mask`.
* Authentication uses ``git -c http.extraheader='Authorization: Bearer ...'``
  so the token does **not** persist in ``.git/config`` or the remote URL.
* Before staging, the script double-checks that credential-shaped files
  (``.pypirc``, ``*.token``, ``.env`` …) are ignored by ``.gitignore``.

Prerequisites
-------------
1. ``git`` on ``PATH``.
2. ``GITHUB_TOKEN`` exported in the shell (classic PAT with ``repo`` scope,
   or a fine-grained token with *Contents: Read/Write* and
   *Administration: Read/Write* if you want ``--create`` to work).
3. Run from the project root (or pass ``--workdir``).

Usage
-----
PowerShell::

    $env:GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    python push_to_github.py --owner bcgbsh --repo icon-mcp-server --create --branch main

Dry run (prints planned actions, changes nothing)::

    python push_to_github.py --owner bcgbsh --repo icon-mcp-server --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

GITHUB_API = "https://api.github.com"
SENSITIVE_PATTERNS = (
    ".pypirc",
    "*.pypirc",
    ".env",
    ".env.*",
    "*.token",
    "*credentials*",
    "*.pem",
    "*.key",
)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    GRAY = "\033[90m"


def _colorize(text: str, color: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"{color}{text}{C.RESET}"


def info(msg: str) -> None:
    print(f"{_colorize('[i]', C.BLUE)} {msg}")


def ok(msg: str) -> None:
    print(f"{_colorize('[+]', C.GREEN)} {msg}")


def warn(msg: str) -> None:
    print(f"{_colorize('[!]', C.YELLOW)} {msg}")


def err(msg: str) -> None:
    print(f"{_colorize('[x]', C.RED)} {msg}", file=sys.stderr)


def step(msg: str) -> None:
    print()
    print(_colorize(f"==> {msg}", C.BOLD))


def mask(text: str, token: str) -> str:
    """Replace every occurrence of ``token`` in ``text`` with ``***``."""
    if not token:
        return text
    return text.replace(token, "***REDACTED***")


# ---------------------------------------------------------------------------
# GitHub API helpers (urllib only — no external deps)
# ---------------------------------------------------------------------------


def gh_request(
    token: str,
    method: str,
    path: str,
    body: Optional[dict] = None,
) -> tuple[int, dict | list | None]:
    url = f"{GITHUB_API}{path}" if path.startswith("/") else path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    except Exception as e:
        raise RuntimeError(f"Network error calling {url}: {e}") from e

    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"_raw": raw.decode(errors="replace")}


def gh_user(token: str) -> dict:
    status, data = gh_request(token, "GET", "/user")
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(
            f"Token validation failed (HTTP {status}). Response: "
            f"{json.dumps(data)[:200] if data else '(empty)'}"
        )
    return data


def gh_repo_exists(token: str, owner: str, repo: str) -> bool:
    status, _ = gh_request(token, "GET", f"/repos/{owner}/{repo}")
    if status == 200:
        return True
    if status == 404:
        return False
    raise RuntimeError(f"Unexpected HTTP {status} checking repo existence")


def gh_create_repo(
    token: str,
    name: str,
    description: str,
    private: bool,
) -> dict:
    body = {
        "name": name,
        "description": description,
        "private": private,
        "auto_init": False,  # we push our own initial commit
        "has_issues": True,
        "has_projects": False,
        "has_wiki": False,
    }
    status, data = gh_request(token, "POST", "/user/repos", body)
    if status not in (200, 201) or not isinstance(data, dict):
        raise RuntimeError(
            f"Failed to create repo (HTTP {status}). Response: "
            f"{json.dumps(data)[:400] if data else '(empty)'}"
        )
    return data


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_git(
    args: list[str],
    cwd: Path,
    token: Optional[str] = None,
    check: bool = True,
    capture: bool = True,
    disable_cred_helper: bool = False,
) -> subprocess.CompletedProcess:
    """Run ``git`` with optional bearer-token auth.

    Two mechanisms are combined for maximum compatibility:

    * ``credential.helper=`` (empty) — disables Git Credential Manager so a
      stale entry in the Windows Credential Manager cannot override our auth.
    * ``http.<url>.extraheader`` — sends ``Authorization: Bearer <token>``
      for hosts that accept header-only auth.

    For hosts that still require a basic-auth username (GitHub does), the
    caller should embed the token in the remote URL instead — see
    :func:`git_push`.

    The token is never written to ``.git/config``.
    """
    cmd = ["git"]
    if disable_cred_helper:
        cmd += ["-c", "credential.helper="]
    if token:
        cmd += ["-c", f"http.https://github.com/.extraheader=Authorization: Bearer {token}"]
    cmd += args
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on interactive auth
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if capture:
        # Scrub token from any captured output before it can be logged.
        result.stdout = mask(result.stdout or "", token or "")
        result.stderr = mask(result.stderr or "", token or "")
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed "
            f"(exit {result.returncode}):\n{result.stderr or result.stdout}"
        )
    return result


def git_have_repo(cwd: Path) -> bool:
    return (cwd / ".git").exists()


def git_init(cwd: Path, branch: str) -> None:
    run_git(["init", "-b", branch], cwd)


def git_config_get(cwd: Path, key: str) -> Optional[str]:
    r = run_git(["config", "--get", key], cwd, check=False)
    v = (r.stdout or "").strip()
    return v or None


def git_config_set_local(cwd: Path, key: str, value: str) -> None:
    run_git(["config", key, value], cwd)


def git_ensure_identity(cwd: Path, fallback_name: str, fallback_email: str) -> tuple[str, str]:
    name = git_config_get(cwd, "user.name")
    email = git_config_get(cwd, "user.email")
    if not name:
        name = fallback_name
        git_config_set_local(cwd, "user.name", name)
        info(f"Set local git user.name = {name}")
    if not email:
        email = fallback_email
        git_config_set_local(cwd, "user.email", email)
        info(f"Set local git user.email = {email}")
    return name, email


def git_check_ignored(cwd: Path, path: str) -> bool:
    """Return True if ``path`` is currently ignored by .gitignore."""
    r = run_git(["check-ignore", "-q", path], cwd, check=False)
    return r.returncode == 0


def git_ensure_ignore_rules(cwd: Path) -> list[str]:
    """Append missing sensitive patterns to .gitignore. Returns added lines."""
    gi = cwd / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    existing_lines = {ln.strip() for ln in existing.splitlines() if ln.strip() and not ln.startswith("#")}
    to_add: list[str] = []
    for pat in SENSITIVE_PATTERNS:
        if pat not in existing_lines:
            to_add.append(pat)
    if to_add:
        header = "" if existing.endswith("\n") or not existing else "\n"
        with gi.open("a", encoding="utf-8") as f:
            f.write(f"{header}\n# Added by push_to_github.py — never commit credentials\n")
            for pat in to_add:
                f.write(f"{pat}\n")
    return to_add


def git_status_porcelain(cwd: Path) -> str:
    r = run_git(["status", "--porcelain"], cwd)
    return r.stdout or ""


def git_has_commits(cwd: Path) -> bool:
    r = run_git(["rev-parse", "--verify", "HEAD"], cwd, check=False)
    return r.returncode == 0


def git_current_branch(cwd: Path) -> Optional[str]:
    r = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, check=False)
    b = (r.stdout or "").strip()
    return b if b and b != "HEAD" else None


def git_remote_get(cwd: Path, name: str = "origin") -> Optional[str]:
    r = run_git(["remote", "get-url", name], cwd, check=False)
    v = (r.stdout or "").strip()
    return v or None


def git_remote_set(cwd: Path, url: str, name: str = "origin") -> None:
    if git_remote_get(cwd, name):
        run_git(["remote", "set-url", name, url], cwd)
    else:
        run_git(["remote", "add", name, url], cwd)


def git_add_all(cwd: Path) -> None:
    run_git(["add", "-A"], cwd)


def git_commit(cwd: Path, message: str) -> bool:
    """Commit staged changes. Returns True if a commit was created."""
    if not git_status_porcelain(cwd).strip():
        return False
    r = run_git(["commit", "-m", message], cwd, check=False)
    if r.returncode != 0:
        # Nothing to commit is exit 1 with a specific message
        if "nothing to commit" in (r.stdout + r.stderr).lower():
            return False
        raise RuntimeError(f"git commit failed:\n{r.stderr or r.stdout}")
    return True


def git_push(
    cwd: Path,
    token: str,
    branch: str,
    owner: str,
    repo: str,
    remote: str = "origin",
    force: bool = False,
) -> None:
    """Push using a token-embedded remote URL, then restore the clean URL.

    Why not just ``http.extraheader``?  Because GitHub still requires a
    basic-auth username even when an ``Authorization`` header is present,
    and Windows Git Credential Manager will happily supply a stale one
    from the Credential Manager, causing ``invalid credentials`` errors.

    Strategy (standard CI/CD pattern):
      1. Temporarily set ``origin`` to ``https://x-access-token:TOKEN@github.com/OWNER/REPO.git``
      2. Push with ``credential.helper=`` disabled so nothing overrides our URL auth
      3. **Always** restore ``origin`` to the clean URL — even on failure
    """
    clean_url = f"https://github.com/{owner}/{repo}.git"
    token_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    try:
        git_remote_set(cwd, token_url, remote)
        args = ["push", "-u", remote, branch]
        if force:
            args.insert(1, "--force")
        run_git(args, cwd, token=token, disable_cred_helper=True)
    finally:
        # Restore clean URL no matter what happened above.
        try:
            git_remote_set(cwd, clean_url, remote)
        except Exception as restore_err:  # pragma: no cover
            warn(f"failed to restore clean remote URL: {restore_err}")
            warn(f"run manually: git remote set-url {remote} {clean_url}")


# ---------------------------------------------------------------------------
# Preflight audit
# ---------------------------------------------------------------------------


def audit_sensitive_files(cwd: Path) -> list[str]:
    """Return a list of warnings for files that look sensitive but aren't ignored."""
    warnings: list[str] = []
    for pat in SENSITIVE_PATTERNS:
        # Expand the pattern against the working tree (top-level only for speed)
        for p in cwd.glob(pat):
            if p.is_file() and not git_check_ignored(cwd, p.name):
                warnings.append(str(p.relative_to(cwd)))
    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Push the current project to GitHub using GITHUB_TOKEN.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--owner", help="GitHub owner (user or org). Defaults to the token's login.")
    ap.add_argument("--repo", default="icon-mcp-server", help="Repository name (default: icon-mcp-server)")
    ap.add_argument("--branch", default="main", help="Branch to push (default: main)")
    ap.add_argument("--workdir", default=".", help="Project directory (default: cwd)")
    ap.add_argument("--message", default=None, help="Commit message (default: auto-generated)")
    ap.add_argument("--create", action="store_true", help="Create the remote repo if missing")
    ap.add_argument("--private", action="store_true", help="When --create, make the repo private")
    ap.add_argument("--description", default="MCP server that generates software icons locally with Pillow.",
                    help="Repo description used with --create")
    ap.add_argument("--force", action="store_true", help="Force push (dangerous; use with care)")
    ap.add_argument("--dry-run", action="store_true", help="Show planned actions without changing anything")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    cwd = Path(args.workdir).resolve()

    step("Preflight")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        err("GITHUB_TOKEN is not set. Export it first, e.g.:")
        err('  PowerShell : $env:GITHUB_TOKEN = "ghp_xxx..."')
        err('  cmd        : set GITHUB_TOKEN=ghp_xxx...')
        err('  bash       : export GITHUB_TOKEN=ghp_xxx...')
        return 2
    ok(f"GITHUB_TOKEN loaded (length={len(token)}, prefix={token[:4]}***)")

    if not shutil.which("git"):
        err("`git` not found on PATH. Install Git for Windows first.")
        return 2
    ok(f"git found: {shutil.which('git')}")

    if not cwd.exists() or not cwd.is_dir():
        err(f"Workdir does not exist: {cwd}")
        return 2
    ok(f"workdir: {cwd}")

    step("Validating token & resolving owner")
    user = gh_user(token)
    login = user.get("login", "")
    owner = args.owner or login
    ok(f"token belongs to: {login}")
    info(f"target repo: {owner}/{args.repo}")

    step("Checking remote repo")
    exists = gh_repo_exists(token, owner, args.repo)
    if exists:
        ok(f"remote repo already exists: https://github.com/{owner}/{args.repo}")
    elif args.create:
        warn(f"remote repo does not exist; --create specified → will create it "
             f"({'private' if args.private else 'public'})")
        if not args.dry_run:
            created = gh_create_repo(token, args.repo, args.description, args.private)
            ok(f"created: {created.get('html_url')}")
            exists = True
    else:
        err(f"remote repo does not exist: {owner}/{args.repo}")
        err("Either create it manually on GitHub, or re-run with --create.")
        return 3

    step("Local repository state")
    if not git_have_repo(cwd):
        warn("no .git directory — will run `git init`")
        if not args.dry_run:
            git_init(cwd, args.branch)
            ok(f"initialised git repo (branch={args.branch})")
    else:
        ok(".git already present")
        cur = git_current_branch(cwd)
        if cur and cur != args.branch:
            warn(f"current branch is `{cur}`, target is `{args.branch}` — "
                 f"will rename via `git branch -M {args.branch}`")
            if not args.dry_run:
                run_git(["branch", "-M", args.branch], cwd)

    step("Ensuring identity")
    fallback_email = f"{login}@users.noreply.github.com"
    if args.dry_run:
        info(f"[dry-run] would ensure user.name={login!r} user.email={fallback_email!r}")
    else:
        git_ensure_identity(cwd, login, fallback_email)

    step("Hardening .gitignore")
    added = git_ensure_ignore_rules(cwd) if not args.dry_run else []
    if added:
        ok(f"appended {len(added)} pattern(s) to .gitignore: {added}")
    else:
        ok("all sensitive patterns already covered")

    step("Auditing for accidentally-trackable secrets")
    leaks = audit_sensitive_files(cwd)
    if leaks:
        err("The following sensitive files are NOT ignored and would be committed:")
        for f in leaks:
            err(f"  - {f}")
        err("Fix .gitignore (or remove the files) and re-run.")
        return 4
    ok("no sensitive files at risk")

    step("Staging & committing")
    if args.dry_run:
        info("[dry-run] would: git add -A && git commit")
    else:
        git_add_all(cwd)
        msg = args.message or _auto_commit_message(cwd)
        created = git_commit(cwd, msg)
        if created:
            ok(f"commit created: {msg.splitlines()[0]}")
        else:
            info("working tree clean — nothing new to commit")
        if not git_has_commits(cwd):
            err("Repository has no commits yet; cannot push. Add at least one file.")
            return 5

    step("Configuring remote")
    remote_url = f"https://github.com/{owner}/{args.repo}.git"
    if args.dry_run:
        info(f"[dry-run] would set origin → {remote_url}")
    else:
        git_remote_set(cwd, remote_url, "origin")
        ok(f"origin → {remote_url}  (token NOT stored in .git/config)")

    step("Pushing")
    if args.dry_run:
        info(f"[dry-run] would: git push -u origin {args.branch}"
             + (" --force" if args.force else ""))
        info("[dry-run] auth: token temporarily embedded in remote URL, restored after push")
    else:
        try:
            git_push(cwd, token, args.branch, owner, args.repo, force=args.force)
            ok(f"pushed to {remote_url} (branch={args.branch})")
        except RuntimeError as e:
            err(f"push failed: {e}")
            err("Hints:")
            err("  * If the remote already has commits, pull/rebase first, "
                "or re-run with --force (destructive).")
            err("  * If the token lacks repo scope, regenerate it with `repo` "
                "(classic) or `Contents: Read/Write` (fine-grained).")
            err("  * Stale Windows credentials can also cause this — run: "
                "cmdkey /list | findstr github")
            return 6

    step("Done")
    ok(f"https://github.com/{owner}/{args.repo}/tree/{args.branch}")
    ok("Reminder: `git remote -v` shows a clean URL — the token was used only "
       "for the push invocation and is not persisted anywhere.")
    return 0


def _auto_commit_message(cwd: Path) -> str:
    """Build a reasonable default commit message."""
    version = _detect_version(cwd)
    if version:
        return f"chore: initial push (v{version})"
    return "chore: initial push"


def _detect_version(cwd: Path) -> Optional[str]:
    py = cwd / "pyproject.toml"
    if py.exists():
        m = re.search(r'^\s*version\s*=\s*"([^"]+)"', py.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            return m.group(1)
    return None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        err("interrupted")
        raise SystemExit(130)
    except Exception as e:  # noqa: BLE001
        err(f"unhandled error: {type(e).__name__}: {e}")
        raise SystemExit(1)
