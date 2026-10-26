"""Credential scan of a Git repository: every blob reachable from every ref, every commit and tag
message, and the working-tree files that would be committed (tracked + untracked, not ignored).

Two detectors:
  1. exact local secret values, read from the git-ignored env files given with --env (values are
     held in memory only and never printed; a hit reports the variable NAME and the location);
  2. credential patterns (OpenAI, GitHub, AWS, Slack, Google, private keys, JWT, ingest keys,
     long Bearer values, URL user-info).
Output: counts and locations only. Exit 1 on any hit.
Usage: python -I scripts/qa/secret_scan.py --repo <dir> --env <file> [--env <file> ...] [--self-test]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "openai_key": re.compile(rb"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"
    ),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack_token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    ),
    "jwt": re.compile(
        rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    # Real ingest keys: lli_<prefix>_<>=43 url-safe chars> (256-bit secret).
    "ingest_key": re.compile(rb"\blli_[A-Za-z0-9]{6,}_[A-Za-z0-9_-]{40,}"),
    "bearer_value": re.compile(rb"Bearer\s+[A-Za-z0-9._~+/=-]{24,}"),
    "url_userinfo": re.compile(
        rb"[a-z][a-z0-9+.-]*://[^\s/:@'\"<>]+:[^\s/@'\"<>$]{8,}@"
    ),
}
# Documented, non-secret look-alikes in this repository (each checked by hand, Phase 5A/5B).
ALLOW = [
    re.compile(rb"Bearer\s+\$\{?LLMI_ADMIN_TOKEN"),
    re.compile(rb"Bearer\s+(?:t{24,}|w{24,})"),  # test dummies t...t / w...w
    re.compile(rb"://[^\s/:@]+:CHANGE_ME@"),
    re.compile(rb"://[^\s/:@]+:\$\{"),  # compose interpolation
]
SECRET_NAMES = re.compile(
    r"(PASSWORD|PEPPER|TOKEN|API_KEY|HEADERS|DATABASE_URL|SECRET)"
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True
    ).stdout


def load_secrets(env_files):
    values = {}
    for f in env_files:
        p = Path(f)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            v = v.strip("'\"")
            if not SECRET_NAMES.search(k) or len(v) < 12 or "CHANGE_ME" in v:
                continue
            values[f"{p.name}:{k}"] = v.encode()
            # Also the secret part of composite values (X-Project-Key=<key>, URL passwords).
            for part in re.split(r"[=:@/]", v):
                if len(part) >= 16 and part != v:
                    values[f"{p.name}:{k}[part]"] = part.encode()
    return values


def scan_bytes(data, secrets):
    hits = []
    for name, val in secrets.items():
        if val in data:
            hits.append(f"secret-value {name}")
    for name, rx in PATTERNS.items():
        for m in rx.finditer(data):
            window = data[max(0, m.start() - 2) : m.end()]
            if any(a.search(window) or a.search(m.group(0)) for a in ALLOW):
                continue
            hits.append(f"pattern {name}")
    return hits


def scan_repo(repo, secrets):
    report = {"blobs": 0, "messages": 0, "files": 0, "hits": []}
    objs = git(repo, "rev-list", "--objects", "--all").decode().splitlines()
    shas = {
        line.split(" ", 1)[0]: (line.split(" ", 1)[1] if " " in line else "")
        for line in objs
    }
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "cat-file",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        input="\n".join(shas).encode(),
        capture_output=True,
        check=True,
    )
    blobs = [
        ln.split()[0]
        for ln in proc.stdout.decode().splitlines()
        if ln.endswith(" blob")
    ]
    for sha in blobs:
        data = git(repo, "cat-file", "blob", sha)
        report["blobs"] += 1
        for h in scan_bytes(data, secrets):
            report["hits"].append(f"{h} in blob {sha[:10]} ({shas.get(sha, '')})")
    for sha in git(repo, "rev-list", "--all").decode().split():
        report["messages"] += 1
        for h in scan_bytes(git(repo, "log", "-1", "--format=%B", sha), secrets):
            report["hits"].append(f"{h} in commit message {sha[:10]}")
    for tag in git(repo, "tag", "-l").decode().split():
        report["messages"] += 1
        for h in scan_bytes(git(repo, "cat-file", "-p", tag), secrets):
            report["hits"].append(f"{h} in tag {tag}")
    files = git(
        repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    ).split(b"\0")
    for rel in filter(None, files):
        p = Path(repo) / rel.decode()
        if p.is_file():
            report["files"] += 1
            for h in scan_bytes(p.read_bytes(), secrets):
                report["hits"].append(f"{h} in working file {rel.decode()}")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--env", action="append", default=[])
    args = ap.parse_args()
    secrets = load_secrets(args.env)
    rep = scan_repo(args.repo, secrets)
    print(
        f"secret values loaded: {len(secrets)} (names only: {sorted({k.split('[')[0] for k in secrets})})\n"
        f"patterns: {len(PATTERNS)}; blobs scanned: {rep['blobs']}; commit/tag messages: {rep['messages']}; "
        f"working files: {rep['files']}; hits: {len(rep['hits'])}"
    )
    for h in rep["hits"]:
        print("HIT", h)
    return 1 if rep["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
