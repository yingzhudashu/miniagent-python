#!/usr/bin/env python3
"""Recursively copy a skill directory from anthropics/skills (or any public repo) to workspaces/skills.

Uses the GitHub contents API (unauthenticated by default). Set ``GITHUB_TOKEN`` for higher rate limits.

Usage:
  python scripts/vendor_skill_from_github.py anthropics/skills skills/skill-creator skill-creator
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


def _github_headers() -> dict[str, str]:
    h = {"User-Agent": "miniagent-vendor-script"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_file(download_url: str, dest: Path) -> None:
    req = urllib.request.Request(download_url, headers=_github_headers())
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.read())


def vendor_tree(owner: str, repo: str, ref: str, remote_prefix: str, dest_root: Path) -> int:
    """Walk GitHub contents API under remote_prefix and write files under dest_root."""
    from urllib.parse import quote

    base = f"https://api.github.com/repos/{owner}/{repo}/contents"
    n = 0
    stack = [remote_prefix.rstrip("/")]
    while stack:
        cur = stack.pop()
        url = f"{base}/{quote(cur, safe='')}?ref={ref}"
        data = _get_json(url)
        if not isinstance(data, list):
            continue
        for item in data:
            path = item["path"]
            typ = item["type"]
            if typ == "dir":
                stack.append(path)
            elif typ == "file":
                rel = path[len(remote_prefix) :].lstrip("/")
                out = dest_root / rel
                du = item.get("download_url")
                if not du:
                    # large file encoding
                    enc = item.get("encoding")
                    content = item.get("content")
                    if enc == "base64" and content:
                        import base64

                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(base64.b64decode(content))
                        n += 1
                    continue
                _download_file(du, out)
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor a skill folder from GitHub into workspaces/skills")
    ap.add_argument("owner_repo", help="owner/repo e.g. anthropics/skills")
    ap.add_argument("remote_prefix", help="path in repo e.g. skills/skill-creator")
    ap.add_argument("local_name", help="directory name under workspaces/skills")
    ap.add_argument("--ref", default="main", help="git ref")
    args = ap.parse_args()
    owner, _, repo = args.owner_repo.partition("/")
    if not owner or not repo:
        print("owner_repo must be owner/repo", file=sys.stderr)
        return 1
    here = Path(__file__).resolve().parent.parent
    dest = here / "workspaces" / "skills" / args.local_name
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    n = vendor_tree(owner, repo, args.ref, args.remote_prefix, dest)
    print(f"Wrote {n} files under {dest}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
