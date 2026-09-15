"""Diagnose the GitHub push auth failure without exposing the token.

Checks:
  1. GITHUB_TOKEN format (length, prefix — no full value)
  2. GET /user  — is the token valid at all?
  3. GET /repos/{owner}/{repo}  — does the repo exist and what perms does the token have?
  4. Token scope header — does it include `repo`?
"""

import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OWNER = "bcgbsh"
REPO = "icon-mcp-server"


def hr(title: str) -> None:
    print()
    print(f"=== {title} ===")


def call(method: str, path: str) -> tuple[int, dict, dict]:
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"_raw": raw.decode(errors="replace")[:400]}
        return e.code, body, dict(e.headers)
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}, {}


def main() -> int:
    hr("1. Token format")
    if not TOKEN:
        print("GITHUB_TOKEN is EMPTY")
        return 2
    print(f"length:  {len(TOKEN)}")
    print(f"prefix:  {TOKEN[:4]}***")
    print(f"suffix:  ***{TOKEN[-3:]}")
    print(f"has_ws:  {TOKEN != TOKEN.strip()}")  # leading/trailing whitespace?
    known_prefixes = {
        "ghp_": "classic PAT (40 chars typical)",
        "github_pat_": "fine-grained PAT",
        "gho_": "OAuth token",
        "ghu_": "GitHub App user token",
        "ghs_": "GitHub App installation token",
        "ghr_": "refresh token",
    }
    kind = next((v for k, v in known_prefixes.items() if TOKEN.startswith(k)), "UNKNOWN")
    print(f"kind:    {kind}")

    hr("2. GET /user — token validity")
    status, body, headers = call("GET", "/user")
    print(f"HTTP {status}")
    if status == 200:
        print(f"login:      {body.get('login')}")
        print(f"id:         {body.get('id')}")
        print(f"name:       {body.get('name')}")
        print(f"scopes_hdr: {headers.get('X-OAuth-Scopes', '(none — fine-grained or no scope header)')}")
        print(f"accepted:   {headers.get('X-Accepted-OAuth-Scopes', '(none)')}")
    else:
        print(f"body: {json.dumps(body)[:400]}")
        print("=> TOKEN IS INVALID / REVOKED / EXPIRED")
        return 3

    hr(f"3. GET /repos/{OWNER}/{REPO} — repo & permissions")
    status, body, headers = call("GET", f"/repos/{OWNER}/{REPO}")
    print(f"HTTP {status}")
    if status == 200:
        print(f"full_name:  {body.get('full_name')}")
        print(f"private:    {body.get('private')}")
        print(f"default_br: {body.get('default_branch')}")
        perms = body.get("permissions", {})
        print(f"permissions: {perms}")
        if not perms.get("push"):
            print("=> TOKEN LACKS PUSH PERMISSION ON THIS REPO")
            print("   (either the token belongs to a different user,")
            print("    or the fine-grained token doesn't include this repo,")
            print("    or Contents permission is Read-only)")
            return 4
    elif status == 404:
        print("=> REPO NOT FOUND (or token can't see it because it's private)")
        print("   If you deleted and recreated, this is expected.")
        return 5
    else:
        print(f"body: {json.dumps(body)[:400]}")
        return 6

    hr("4. Verdict")
    print("Token appears VALID and has push permission.")
    print("If `git push` still fails with 'invalid credentials',")
    print("the likely cause is:")
    print("  * Token was pasted with hidden whitespace (check has_ws above)")
    print("  * A stale credential in Windows Credential Manager is being used")
    print("    instead of your http.extraheader — clear it with:")
    print("      cmdkey /list | findstr github")
    print("      cmdkey /delete:git:https://github.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
