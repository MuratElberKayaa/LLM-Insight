"""Fail-closed Git history secret-scan gate (R104-3). Stdlib only; Git runs through secret_scan.git.

The detectors are secret_scan.PATTERNS, applied raw: the scanner's ALLOW list is NOT used here, so
no global regular expression can accept a match. Every match must equal one reviewed entry of
security/secret-history-baseline.json, identified by detector, object type, Git object id,
occurrence, byte offset, length and (for a blob) the exact set of repository paths. Matched bytes
are never stored or printed: the object id and offset already pin them.

Scope: every object reachable from every ref of the checkout (HEAD included): blobs, commit objects
(headers and message), annotated tag objects (nested included) and tree objects (entry names), plus
the ref names. Not covered, and never claimed: refs the checkout did not fetch (GitHub-managed
refs/pull/*/head of other PRs), unreachable objects, content inside archives or encoded data, and
the exact local secret values of secret_scan.py (CI has no local env files).

Subcommands: coverage, scan, validate-baseline, evaluate (CI); candidates, register (local review
aids that never change the baseline). Any problem exits 1. Run under python -I.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "secret_scan", Path(__file__).resolve().with_name("secret_scan.py")
)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)

SCHEMA_BASELINE = "llm-insight/secret-history-baseline/v1"
SCHEMA_COVERAGE = "llm-insight/secret-history-coverage/v1"
SCHEMA_SCAN = "llm-insight/secret-history-scan/v1"
SCHEMA_SUMMARY = "llm-insight/secret-history-summary/v1"

# SHA-256 of the canonical reviewed baseline. Changing the baseline therefore also needs a reviewed
# change of this constant and of the docs/SECURITY.md register; CI never regenerates any of them.
REVIEWED_BASELINE_SHA256 = (
    "203d442266a1a5eb2594ac5141c8cc201cbc81bb7f3ea9e33c8c1a9d2ea153f7"
)

OID = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ENTRY_ID = re.compile(r"SH-[0-9]{2,4}\Z")
SAFE_PATH = re.compile(r"[A-Za-z0-9._/@+-]+\Z")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
OBJECT_TYPES = ("commit", "tag", "tree", "blob")
REF_CATEGORIES = (
    ("refs/remotes/pull/", "pull"),
    ("refs/pull/", "pull"),
    ("refs/remotes/", "remote"),
    ("refs/heads/", "branch"),
    ("refs/tags/", "tag"),
)
ALLOWED_REF_PREFIXES = sorted(prefix for prefix, _ in REF_CATEGORIES)
REGISTER_BEGIN = "<!-- secret-history-baseline:begin -->"
REGISTER_END = "<!-- secret-history-baseline:end -->"

BASELINE_KEYS = {
    "schema",
    "tool",
    "scope",
    "generated",
    "review",
    "totals",
    "findings",
}
ENTRY_KEYS = {
    "id",
    "detector",
    "object_type",
    "object_id",
    "occurrence",
    "offset",
    "length",
    "paths",
    "classification",
    "reason",
    "review_ref",
}
SCOPE_KEYS = {
    "ref_prefixes",
    "required_refs",
    "required_tags",
    "root_commits",
    "exclusions",
}
COVERAGE_KEYS = {
    "schema",
    "status",
    "problems",
    "head",
    "refs_digest",
    "object_digest",
    "scope_digest",
    "ref_categories",
    "counts",
    "roots",
    "gitlinks",
    "nested_tags",
    "unreachable_objects",
    "expected_commits_verified",
    "shallow",
    "partial",
}
SCAN_KEYS = {
    "schema",
    "status",
    "problems",
    "head",
    "refs_digest",
    "object_digest",
    "detector_digest",
    "detectors",
    "scanned",
    "exclusions",
    "findings",
}
FINDING_KEYS = {
    "detector",
    "object_type",
    "object_id",
    "occurrence",
    "offset",
    "length",
    "paths",
}

# Closed classes. Each is limited to its detector, object type, paths and review reference, so a
# baseline cannot accept a match of another kind or place by naming one of these classes.
REVIEW_F9 = "docs/reports/phase-5b.md section 4 F9; docs/reports/r104.md R104-3"
CLASSES = {
    "F9-DOC-EXAMPLE": {
        "detectors": {"url_userinfo"},
        "object_types": {"blob"},
        "paths": {
            "docs/SECURITY.md",
            "docs/reports/phase-2c.md",
            "backend/tests/redaction.py",
            "sdk/tests/redaction.py",
        },
        "review_ref": REVIEW_F9,
    },
    "F9-TEST-FIXTURE": {
        "detectors": {"url_userinfo"},
        "object_types": {"blob"},
        "paths": {
            "backend/tests/unit/test_config.py",
            "backend/tests/unit/test_output_redaction.py",
            "sdk/tests/unit/test_redaction.py",
        },
        "review_ref": REVIEW_F9,
    },
    "F9-CHANGE-ME-VALUE": {
        "detectors": {"url_userinfo"},
        "object_types": {"blob"},
        "paths": {".env.example", "backend/tests/unit/test_config.py"},
        "review_ref": REVIEW_F9,
    },
}

LIMITATIONS = [
    "Covers only objects reachable from the refs present in this checkout.",
    "GitHub-managed refs of other pull requests and unreachable objects are not fetched or scanned.",
    "Pattern detection only: exact local secret values are not available in CI.",
    "Content inside archives or encoded data is not decoded.",
    "A clean result does not prove that no secret exists in the history.",
]


class GitFailure(Exception):
    """A Git command failed; the message holds a fixed category, never Git's own output."""


def problem(kind, detail=""):
    text = str(detail)
    if any(True for _ in ss.iter_matches(text.encode("utf-8", "backslashreplace"))):
        text = "value-redacted"
    return {"kind": kind, "detail": re.sub(r"[^A-Za-z0-9._/@+=, -]", "?", text)[:300]}


def safe_path(raw):
    """A printable repository path, or a marker when it is unusual or matches a detector."""
    if any(True for _ in ss.iter_matches(raw)):
        return "<redacted-path>"
    text = raw.decode("utf-8", "backslashreplace")
    return text if SAFE_PATH.match(text) else "<unprintable-path>"


def canonical(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def run_git(repo, *args):
    try:
        return ss.git(repo, *args)
    except ss.subprocess.CalledProcessError as exc:
        raise GitFailure(f"git {args[0]} exit status {exc.returncode}") from None
    except OSError:
        raise GitFailure(f"git {args[0]} could not be started") from None


def read_objects(repo):
    """All objects of the repository, from one strictly parsed `cat-file --batch` stream."""
    raw = run_git(repo, "cat-file", "--batch", "--batch-all-objects", "--unordered")
    objects, pos = {}, 0
    while pos < len(raw):
        newline = raw.find(b"\n", pos)
        parts = raw[pos:newline].split(b" ") if newline >= 0 else []
        if (
            len(parts) != 3
            or not parts[2].isdigit()
            or not OID.match(parts[0].decode("ascii", "replace"))
            or parts[1].decode("ascii", "replace") not in OBJECT_TYPES
        ):
            raise GitFailure("malformed object stream from git cat-file")
        end = newline + 1 + int(parts[2])
        if raw[end : end + 1] != b"\n":
            raise GitFailure("truncated object stream from git cat-file")
        objects[parts[0].decode()] = (parts[1].decode(), raw[newline + 1 : end])
        pos = end + 1
    return objects


def header_oid(line, prefix):
    oid = line[len(prefix) :].decode("ascii", "replace")
    if not OID.match(oid):
        raise GitFailure("malformed object header")
    return oid


def parse_commit(data):
    tree, parents = None, []
    for line in data.split(b"\n\n", 1)[0].split(b"\n"):
        if line.startswith(b"tree "):
            tree = header_oid(line, b"tree ")
        elif line.startswith(b"parent "):
            parents.append(header_oid(line, b"parent "))
    if tree is None:
        raise GitFailure("malformed commit object")
    return tree, parents


def parse_tag(data):
    target, kind = None, None
    for line in data.split(b"\n\n", 1)[0].split(b"\n"):
        if line.startswith(b"object "):
            target = header_oid(line, b"object ")
        elif line.startswith(b"type "):
            kind = line[5:].decode("ascii", "replace")
    if target is None or kind not in OBJECT_TYPES:
        raise GitFailure("malformed tag object")
    return target, kind


def parse_tree(data):
    entries, pos = [], 0
    while pos < len(data):
        space = data.find(b" ", pos)
        nul = data.find(b"\0", space)
        if space < 0 or nul < 0 or nul + 21 > len(data):
            raise GitFailure("malformed tree object")
        entries.append(
            (data[pos:space], data[space + 1 : nul], data[nul + 1 : nul + 21].hex())
        )
        pos = nul + 21
    return entries


def fetch(objects, oid, kind):
    if oid == EMPTY_TREE and kind == "tree":
        return b""
    if oid not in objects:
        raise GitFailure("object missing from the repository")
    if objects[oid][0] != kind:
        raise GitFailure("object has an unexpected type")
    return objects[oid][1]


def walk(objects, refs):
    """Independent enumeration: parses commits, tags and trees, starting from the ref targets."""
    found = {kind: set() for kind in OBJECT_TYPES}
    paths = defaultdict(set)
    counts = {"gitlinks": 0, "nested_tags": 0}
    roots = set()
    memo = {}

    def tree_entries(oid):
        if oid not in memo:
            found["tree"].add(oid)
            entries = []
            for mode, name, child in parse_tree(fetch(objects, oid, "tree")):
                if mode == b"160000":
                    counts["gitlinks"] += 1
                elif mode == b"40000":
                    entries += [
                        (name + b"/" + sub, blob) for sub, blob in tree_entries(child)
                    ]
                else:
                    fetch(objects, child, "blob")
                    found["blob"].add(child)
                    entries.append((name, child))
            memo[oid] = entries
        return memo[oid]

    stack = []
    for oid, kind, _name in refs:
        depth = 0
        while kind == "tag":
            found["tag"].add(oid)
            oid, kind = parse_tag(fetch(objects, oid, "tag"))
            depth += 1
        counts["nested_tags"] += max(0, depth - 1)
        if kind == "commit":
            stack.append(oid)
        elif kind == "tree":
            tree_entries(oid)
        else:
            fetch(objects, oid, "blob")
            found["blob"].add(oid)
    while stack:
        oid = stack.pop()
        if oid in found["commit"]:
            continue
        found["commit"].add(oid)
        tree, parents = parse_commit(fetch(objects, oid, "commit"))
        if not parents:
            roots.add(oid)
        for path, blob in tree_entries(tree):
            paths[blob].add(path)
        stack += parents
    return found, paths, counts, roots


def ref_category(name):
    for prefix, category in REF_CATEGORIES:
        if name.startswith(prefix):
            return category
    return None


def enumerate_repo(repo):
    """Every ref and reachable object, found twice: `rev-list --objects` and the parsed walk."""
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"
    problems, refs = [], []
    output = run_git(
        repo, "for-each-ref", "--format=%(objectname) %(objecttype) %(refname)"
    )
    for line in output.decode("utf-8", "replace").splitlines():
        fields = line.split(" ", 2)
        if (
            len(fields) != 3
            or not OID.match(fields[0])
            or fields[1] not in OBJECT_TYPES
        ):
            raise GitFailure("malformed output from git for-each-ref")
        refs.append(tuple(fields))
    head = run_git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if not OID.match(head):
        raise GitFailure("malformed output from git rev-parse")
    listed = set()
    rev_list = run_git(
        repo, "rev-list", "--objects", "--no-object-names", "--all", "--missing=print"
    )
    for line in rev_list.decode("ascii", "replace").splitlines():
        if line.startswith("?"):
            problems.append(
                problem("MISSING_OBJECT", "an object reachable from a ref is absent")
            )
        elif OID.match(line):
            listed.add(line)
        else:
            raise GitFailure("malformed output from git rev-list")
    all_objects = read_objects(repo)
    if EMPTY_TREE in listed:
        all_objects.setdefault(EMPTY_TREE, ("tree", b""))
    absent = listed - set(all_objects)
    if absent:
        problems.append(
            problem("MISSING_OBJECT", f"{len(absent)} listed objects are not readable")
        )
    listed &= set(all_objects)
    objects = {oid: all_objects[oid] for oid in listed}
    found, paths, counts, roots = walk(all_objects, refs + [(head, "commit", "HEAD")])
    for kind in OBJECT_TYPES:
        expected = {oid for oid in listed if objects[oid][0] == kind}
        if expected != found[kind]:
            problems.append(
                problem(
                    "ENUMERATION_MISMATCH",
                    f"{kind}: rev-list and the parsed walk differ",
                )
            )
    return {
        "head": head,
        "refs": refs,
        "objects": objects,
        "paths": paths,
        "problems": problems,
        "counts": {
            kind: sum(1 for value in objects.values() if value[0] == kind)
            for kind in OBJECT_TYPES
        },
        "walk": counts,
        "roots": sorted(roots),
        "unreachable": len(all_objects) - len(objects),
        "refs_digest": digest(sorted([name, oid] for oid, _kind, name in refs)),
        "object_digest": digest(sorted(objects)),
    }


def failed_coverage(problems, scope_digest="", shallow=False, partial=False):
    return {
        "schema": SCHEMA_COVERAGE,
        "status": "FAILED",
        "problems": problems,
        "head": "",
        "refs_digest": "",
        "object_digest": "",
        "scope_digest": scope_digest,
        "ref_categories": {},
        "counts": {},
        "roots": [],
        "gitlinks": 0,
        "nested_tags": 0,
        "unreachable_objects": 0,
        "expected_commits_verified": 0,
        "shallow": shallow,
        "partial": partial,
    }


def coverage_report(repo, scope, expect_commits, expect_head):
    scope_digest = digest(scope)
    try:
        # An incomplete checkout is declared first: enumerating one would only fail obscurely.
        shallow = (
            run_git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true"
        )
        names = run_git(repo, "config", "--local", "--list", "--name-only").decode(
            "utf-8", "replace"
        )
        partial = any(
            key == "extensions.partialclone"
            or re.fullmatch(r"remote\..*\.(promisor|partialclonefilter)", key)
            for key in names.lower().splitlines()
        )
        if shallow or partial:
            problems = []
            if shallow:
                problems.append(problem("SHALLOW_HISTORY", "the checkout is shallow"))
            if partial:
                problems.append(
                    problem("PARTIAL_CLONE", "the checkout is a partial clone")
                )
            return failed_coverage(problems, scope_digest, shallow, partial)
        grafts = (
            Path(repo)
            / run_git(repo, "rev-parse", "--git-path", "info/grafts").decode().strip()
        )
        info = enumerate_repo(repo)
    except (GitFailure, UnicodeDecodeError, RecursionError) as exc:
        return failed_coverage([problem("GIT_FAILURE", exc)], scope_digest)
    problems = list(info["problems"])
    if grafts.exists():
        problems.append(problem("GRAFTS", "a grafts file rewrites the visible history"))
    categories = Counter()
    by_name = {}
    for oid, kind, name in info["refs"]:
        by_name[name] = oid
        category = ref_category(name)
        if category is None:
            problems.append(
                problem("UNEXPECTED_REF", "namespace " + "/".join(name.split("/")[:2]))
            )
            categories["other"] += 1
        else:
            categories[category] += 1
        if kind not in ("commit", "tag"):
            problems.append(
                problem("UNEXPECTED_REF_TARGET", f"a ref points to a {kind}")
            )
        if any(True for _ in ss.iter_matches(name.encode("utf-8", "replace"))):
            problems.append(problem("REF_NAME_MATCH", "a ref name matches a detector"))
    for name in scope["required_refs"]:
        if name not in by_name:
            problems.append(problem("REQUIRED_REF_MISSING", name))
    for tag, oid in scope["required_tags"].items():
        current = by_name.get("refs/tags/" + tag)
        if current is None:
            problems.append(problem("REQUIRED_TAG_MISSING", tag))
        elif current != oid:
            problems.append(problem("REQUIRED_TAG_MOVED", tag))
    if info["roots"] != sorted(scope["root_commits"]):
        problems.append(
            problem("ROOT_COMMITS_DIFFER", "the root commits differ from the scope")
        )
    if info["walk"]["gitlinks"]:
        problems.append(
            problem("GITLINK", f"{info['walk']['gitlinks']} submodule entries")
        )
    if not info["counts"]["commit"] or not info["counts"]["blob"]:
        problems.append(problem("EMPTY_SCOPE", "no commit or no blob was enumerated"))
    verified = 0
    commits = {oid for oid, value in info["objects"].items() if value[0] == "commit"}
    for oid in expect_commits:
        if not OID.match(oid):
            problems.append(
                problem("EXPECTED_COMMIT_INVALID", "not a 40-hex commit id")
            )
        elif oid not in commits:
            problems.append(problem("EXPECTED_COMMIT_UNREACHABLE", oid))
        else:
            verified += 1
    if expect_head is not None and expect_head != info["head"]:
        problems.append(
            problem("HEAD_MISMATCH", "HEAD differs from the expected commit")
        )
    return {
        "schema": SCHEMA_COVERAGE,
        "status": "FAILED" if problems else "COMPLETE",
        "problems": problems,
        "head": info["head"],
        "refs_digest": info["refs_digest"],
        "object_digest": info["object_digest"],
        "scope_digest": scope_digest,
        "ref_categories": dict(sorted(categories.items())),
        "counts": info["counts"],
        "roots": info["roots"],
        "gitlinks": info["walk"]["gitlinks"],
        "nested_tags": info["walk"]["nested_tags"],
        "unreachable_objects": info["unreachable"],
        "expected_commits_verified": verified,
        "shallow": shallow,
        "partial": partial,
    }


def failed_scan(problems):
    return {
        "schema": SCHEMA_SCAN,
        "status": "FAILED",
        "problems": problems,
        "head": "",
        "refs_digest": "",
        "object_digest": "",
        "detector_digest": ss.detector_digest(),
        "detectors": sorted(ss.PATTERNS),
        "scanned": {},
        "exclusions": [],
        "findings": [],
    }


def scan_report(repo):
    try:
        info = enumerate_repo(repo)
    except (GitFailure, UnicodeDecodeError, RecursionError) as exc:
        return failed_scan([problem("GIT_FAILURE", exc)])
    findings, scanned = [], Counter()
    for oid, (kind, data) in sorted(info["objects"].items()):
        scanned[kind] += 1
        for detector, occurrence, offset, length in ss.iter_matches(data):
            paths = (
                sorted({safe_path(p) for p in info["paths"].get(oid, ())})
                if kind == "blob"
                else []
            )
            findings.append(
                {
                    "detector": detector,
                    "object_type": kind,
                    "object_id": oid,
                    "occurrence": occurrence,
                    "offset": offset,
                    "length": length,
                    "paths": paths,
                }
            )
    return {
        "schema": SCHEMA_SCAN,
        "status": "FAILED" if info["problems"] else "COMPLETE",
        "problems": info["problems"],
        "head": info["head"],
        "refs_digest": info["refs_digest"],
        "object_digest": info["object_digest"],
        "detector_digest": ss.detector_digest(),
        "detectors": sorted(ss.PATTERNS),
        "scanned": {kind: scanned.get(kind, 0) for kind in OBJECT_TYPES},
        "exclusions": [],
        "findings": findings,
    }


def is_count(value):
    return type(value) is int and value >= 0


def valid_path(path):
    return (
        isinstance(path, str)
        and SAFE_PATH.match(path) is not None
        and not path.startswith("/")
        and ".." not in path.split("/")
    )


def check_entry(entry, index):
    """Problems of one baseline entry against the schema and the closed classes."""
    where = f"entry {index}"
    if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
        return [
            problem(
                "BASELINE_INVALID", where + " does not have exactly the required keys"
            )
        ]
    bad = []
    if not (isinstance(entry["id"], str) and ENTRY_ID.match(entry["id"])):
        bad.append("id")
    if not (isinstance(entry["detector"], str) and entry["detector"] in ss.PATTERNS):
        bad.append("detector")
    if not (
        isinstance(entry["object_type"], str) and entry["object_type"] in OBJECT_TYPES
    ):
        bad.append("object_type")
    if not (isinstance(entry["object_id"], str) and OID.match(entry["object_id"])):
        bad.append("object_id")
    if not is_count(entry["occurrence"]) or not is_count(entry["offset"]):
        bad.append("position")
    if not is_count(entry["length"]) or not 1 <= entry["length"] <= 4096:
        bad.append("length")
    paths = entry["paths"]
    if (
        not isinstance(paths, list)
        or not all(valid_path(p) for p in paths)
        or paths != sorted(set(paths))
        or (entry["object_type"] == "blob") != bool(paths)
    ):
        bad.append("paths")
    if not (isinstance(entry["reason"], str) and 10 <= len(entry["reason"]) <= 200):
        bad.append("reason")
    if bad:
        return [problem("BASELINE_INVALID", where + " has invalid " + ", ".join(bad))]
    spec = (
        CLASSES.get(entry["classification"])
        if isinstance(entry["classification"], str)
        else None
    )
    if (
        spec is None
        or entry["detector"] not in spec["detectors"]
        or entry["object_type"] not in spec["object_types"]
        or not set(paths) <= spec["paths"]
        or entry["review_ref"] != spec["review_ref"]
    ):
        return [problem("UNKNOWN_CLASSIFICATION", entry["id"])]
    return []


def entry_key(entry):
    return (
        entry["detector"],
        entry["object_type"],
        entry["object_id"],
        entry["occurrence"],
        entry["offset"],
        entry["length"],
    )


def compute_totals(findings):
    return {
        "total": len(findings),
        "by_detector": dict(sorted(Counter(e["detector"] for e in findings).items())),
        "by_object_type": dict(
            sorted(Counter(e["object_type"] for e in findings).items())
        ),
        "by_classification": dict(
            sorted(Counter(e["classification"] for e in findings).items())
        ),
    }


def render_register(findings):
    lines = [
        "| ID | Detector | Object | Id | Occurrence | Offset / length | Paths | Class |"
    ]
    lines.append("|---|---|---|---|---|---|---|---|")
    for e in findings:
        paths = ", ".join(f"`{p}`" for p in e["paths"]) or "-"
        lines.append(
            f"| {e['id']} | {e['detector']} | {e['object_type']} | `{e['object_id'][:12]}` "
            f"| {e['occurrence']} | {e['offset']} / {e['length']} | {paths} | {e['classification']} |"
        )
    return lines


def check_register(findings, doc_path):
    try:
        text = Path(doc_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [problem("REGISTER_MISSING", "the security document cannot be read")]
    if text.count(REGISTER_BEGIN) != 1 or text.count(REGISTER_END) != 1:
        return [problem("REGISTER_MISSING", "register markers not found exactly once")]
    body = text.split(REGISTER_BEGIN, 1)[1].split(REGISTER_END, 1)[0]
    rows = [line.strip() for line in body.splitlines() if line.strip()]
    if rows != render_register(findings):
        return [problem("REGISTER_MISMATCH", "the register differs from the baseline")]
    return []


def load_baseline(path, doc_path):
    """(baseline or None, problems). Every deviation is a problem; nothing is repaired."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None, [problem("BASELINE_MISSING", "the baseline file cannot be read")]
    try:
        baseline = json.loads(raw)
    except ValueError:
        return None, [problem("BASELINE_MALFORMED", "the baseline is not valid JSON")]
    if not isinstance(baseline, dict) or set(baseline) != BASELINE_KEYS:
        return None, [problem("BASELINE_INVALID", "unexpected top-level keys")]
    problems = []
    if baseline["schema"] != SCHEMA_BASELINE:
        problems.append(problem("BASELINE_INVALID", "unknown schema"))
    if any(True for _ in ss.iter_matches(raw)):
        problems.append(
            problem("BASELINE_INVALID", "the baseline text matches a detector")
        )
    tool = baseline["tool"]
    if (
        not isinstance(tool, dict)
        or tool.get("detector_digest") != ss.detector_digest()
    ):
        problems.append(
            problem("DETECTOR_CHANGED", "the detectors differ from the reviewed ones")
        )
    scope = baseline["scope"]
    if (
        not isinstance(scope, dict)
        or set(scope) != SCOPE_KEYS
        or scope["ref_prefixes"] != ALLOWED_REF_PREFIXES
        or scope["exclusions"] != []
        or not isinstance(scope["required_refs"], list)
        or not all(
            isinstance(r, str) and ref_category(r) for r in scope["required_refs"]
        )
        or not isinstance(scope["required_tags"], dict)
        or not all(
            isinstance(oid, str) and OID.match(oid)
            for oid in scope["required_tags"].values()
        )
        or not isinstance(scope["root_commits"], list)
        or not scope["root_commits"]
        or not all(
            isinstance(oid, str) and OID.match(oid) for oid in scope["root_commits"]
        )
    ):
        return None, problems + [problem("BASELINE_INVALID", "invalid scan scope")]
    findings = baseline["findings"]
    if not isinstance(findings, list):
        return None, problems + [problem("BASELINE_INVALID", "findings is not a list")]
    entry_problems = [p for i, e in enumerate(findings) for p in check_entry(e, i)]
    problems += entry_problems
    if entry_problems:
        return None, problems
    ids = Counter(e["id"] for e in findings)
    keys = Counter(entry_key(e) for e in findings)
    for entry_id, count in sorted(ids.items()):
        if count > 1:
            problems.append(problem("BASELINE_DUPLICATE", "repeated id " + entry_id))
    if any(count > 1 for count in keys.values()):
        problems.append(problem("BASELINE_DUPLICATE", "repeated occurrence identity"))
    if baseline["totals"] != compute_totals(findings):
        problems.append(problem("BASELINE_INVALID", "totals differ from the entries"))
    if digest(baseline) != REVIEWED_BASELINE_SHA256:
        problems.append(
            problem(
                "BASELINE_UNREVIEWED",
                "the baseline digest differs from the reviewed one",
            )
        )
    problems += check_register(findings, doc_path)
    return baseline, problems


def load_report(path, schema, keys, label):
    try:
        report = json.loads(Path(path).read_bytes())
    except OSError:
        return None, [problem(label + "_MISSING", "the report cannot be read")]
    except ValueError:
        return None, [problem(label + "_MALFORMED", "the report is not valid JSON")]
    if (
        not isinstance(report, dict)
        or set(report) != keys
        or report["schema"] != schema
    ):
        return None, [problem(label + "_MALFORMED", "unexpected report shape")]
    if report["status"] not in ("COMPLETE", "FAILED") or not isinstance(
        report["problems"], list
    ):
        return None, [problem(label + "_MALFORMED", "unexpected status")]
    return report, []


def check_counts(counts):
    return (
        isinstance(counts, dict)
        and set(counts) == set(OBJECT_TYPES)
        and all(is_count(value) for value in counts.values())
    )


def valid_findings(findings):
    if not isinstance(findings, list):
        return False
    for f in findings:
        if not isinstance(f, dict) or set(f) != FINDING_KEYS:
            return False
        if (
            not isinstance(f["detector"], str)
            or not isinstance(f["object_type"], str)
            or f["detector"] not in ss.PATTERNS
            or f["object_type"] not in OBJECT_TYPES
            or not (isinstance(f["object_id"], str) and OID.match(f["object_id"]))
            or not is_count(f["occurrence"])
            or not is_count(f["offset"])
            or not is_count(f["length"])
            or not isinstance(f["paths"], list)
        ):
            return False
    return True


def reported_problems(report, label):
    out = []
    for item in report["problems"]:
        kind = item.get("kind", "") if isinstance(item, dict) else ""
        detail = item.get("detail", "") if isinstance(item, dict) else ""
        if not re.fullmatch(r"[A-Z_]{1,40}", str(kind)):
            kind = "UNKNOWN"
        out.append(problem(f"{label}_{kind}", detail))
    return out


def compare(baseline, scan):
    """Exact comparison of the reported findings and the baseline entries."""
    problems, rows = [], []
    entries = {entry_key(e): e for e in baseline["findings"]}
    seen = set()
    for finding in scan["findings"]:
        key = entry_key(finding)
        entry = entries.get(key)
        short = f"{finding['object_type']} {finding['object_id'][:12]}"
        where = f"{finding['detector']} in {short} occurrence {finding['occurrence']}"
        if key in seen:
            problems.append(problem("DUPLICATE_FINDING", where))
        elif entry is None:
            problems.append(problem("NEW_FINDING", where))
            rows.append({**finding, "status": "NEW", "id": "", "classification": ""})
        elif finding["paths"] != entry["paths"]:
            problems.append(problem("PATH_MISMATCH", f"{entry['id']} {where}"))
            rows.append(
                {
                    **finding,
                    "status": "PATH_MISMATCH",
                    "id": entry["id"],
                    "classification": "",
                }
            )
        else:
            rows.append(
                {
                    **finding,
                    "status": "ACCEPTED",
                    "id": entry["id"],
                    "classification": entry["classification"],
                }
            )
        seen.add(key)
    for key, entry in sorted(entries.items()):
        if key not in seen:
            problems.append(problem("MISSING_FINDING", entry["id"]))
            rows.append(
                {
                    "detector": entry["detector"],
                    "object_type": entry["object_type"],
                    "object_id": entry["object_id"],
                    "occurrence": entry["occurrence"],
                    "offset": entry["offset"],
                    "length": entry["length"],
                    "paths": entry["paths"],
                    "status": "MISSING",
                    "id": entry["id"],
                    "classification": entry["classification"],
                }
            )
    return problems, rows


def evaluate(args):
    baseline, problems = load_baseline(args.baseline, args.security_doc)
    coverage, found = load_report(
        args.coverage, SCHEMA_COVERAGE, COVERAGE_KEYS, "COVERAGE"
    )
    problems += found
    scan, found = load_report(args.scan, SCHEMA_SCAN, SCAN_KEYS, "SCAN")
    problems += found
    if coverage is not None:
        if coverage["status"] != "COMPLETE":
            problems.append(
                problem("COVERAGE_INCOMPLETE", "the coverage check did not pass")
            )
            problems += reported_problems(coverage, "COVERAGE")
        elif not check_counts(coverage["counts"]):
            problems.append(problem("COVERAGE_MALFORMED", "invalid counts"))
            coverage = None
    if scan is not None:
        if scan["status"] != "COMPLETE":
            problems.append(
                problem("SCAN_INCOMPLETE", "the history scan did not complete")
            )
            problems += reported_problems(scan, "SCAN")
        elif not check_counts(scan["scanned"]) or not valid_findings(scan["findings"]):
            problems.append(problem("SCAN_MALFORMED", "invalid counts or findings"))
            scan = None
    if scan is not None and scan["exclusions"] != []:
        problems.append(
            problem("UNEXPECTED_EXCLUSION", "the scan declares an exclusion")
        )
    if scan is not None and (
        scan["detector_digest"] != ss.detector_digest()
        or scan["detectors"] != sorted(ss.PATTERNS)
    ):
        problems.append(problem("DETECTOR_CHANGED", "the scan used other detectors"))
    if (
        coverage is not None
        and baseline is not None
        and coverage["scope_digest"] != digest(baseline["scope"])
    ):
        problems.append(problem("SCOPE_MISMATCH", "coverage used another scope"))
    usable = (
        coverage is not None
        and scan is not None
        and coverage["status"] == "COMPLETE"
        and scan["status"] == "COMPLETE"
    )
    if usable:
        for field in ("head", "refs_digest", "object_digest"):
            if coverage[field] != scan[field]:
                problems.append(
                    problem("SCOPE_MISMATCH", f"{field} differs between the reports")
                )
        if coverage["counts"] != scan["scanned"]:
            problems.append(
                problem("SCOPE_MISMATCH", "scanned counts differ from the coverage")
            )
    rows = []
    if baseline is not None and scan is not None and scan["status"] == "COMPLETE":
        found, rows = compare(baseline, scan)
        problems += found
    return write_summary(args.out_dir, coverage, scan, baseline, problems, rows)


def write_summary(out_dir, coverage, scan, baseline, problems, rows):
    verdict = "PASS" if not problems else "FAIL"
    status_counts = Counter(row["status"] for row in rows)
    summary = {
        "schema": SCHEMA_SUMMARY,
        "verdict": verdict,
        "scanned_ref_categories": coverage["ref_categories"] if coverage else {},
        "object_counts": scan["scanned"] if scan else {},
        "unreachable_objects_not_scanned": coverage["unreachable_objects"]
        if coverage
        else None,
        "finding_count": len(scan["findings"]) if scan else None,
        "baseline_entries": len(baseline["findings"]) if baseline else None,
        "baseline_matching": dict(sorted(status_counts.items())),
        "problems": problems,
        "findings": [
            {
                "id": row["id"],
                "status": row["status"],
                "detector": row["detector"],
                "object_type": row["object_type"],
                "object": row["object_id"][:12],
                "occurrence": row["occurrence"],
                "paths": row["paths"],
                "classification": row["classification"],
            }
            for row in rows
        ],
        "limitations": LIMITATIONS,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    md = [
        "# History secret-scan gate",
        "",
        f"- Verdict: **{verdict}**",
        f"- Ref categories: {summary['scanned_ref_categories']}",
        f"- Objects scanned: {summary['object_counts']}",
        f"- Findings: {summary['finding_count']}; baseline entries: {summary['baseline_entries']}",
        f"- Baseline matching: {summary['baseline_matching']}",
        f"- Problems: {len(problems)}",
        "",
    ]
    md += [f"- `{p['kind']}` {p['detail']}" for p in problems]
    md += ["", "Known limitations:"] + [f"- {text}" for text in LIMITATIONS] + [""]
    text = "\n".join(md)
    (out / "summary.md").write_text(text, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    report_problems(problems)
    print(
        f"history secret-scan gate: {verdict}; findings {summary['finding_count']}, "
        f"baseline entries {summary['baseline_entries']}, problems {len(problems)}"
    )
    return 1 if problems else 0


def report_problems(problems):
    for item in problems:
        print(f"::error title=secret-history-gate::{item['kind']} {item['detail']}")


def write_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def cmd_coverage(args):
    baseline, problems = load_baseline(args.baseline, args.security_doc)
    if baseline is None:
        report = failed_coverage(problems)
    else:
        report = coverage_report(
            args.repo, baseline["scope"], args.expect_commit, args.expect_head
        )
    write_report(args.out, report)
    report_problems(report["problems"])
    print(
        f"history coverage: {report['status']}; refs {report['ref_categories']}, objects {report['counts']}"
    )
    return 0 if report["status"] == "COMPLETE" else 1


def cmd_scan(args):
    report = scan_report(args.repo)
    write_report(args.out, report)
    report_problems(report["problems"])
    print(
        f"history scan: {report['status']}; objects {report['scanned']}, findings {len(report['findings'])}"
    )
    return 0 if report["status"] == "COMPLETE" else 1


def cmd_validate(args):
    baseline, problems = load_baseline(args.baseline, args.security_doc)
    report_problems(problems)
    if problems or baseline is None:
        return 1
    print(f"baseline valid: {baseline['totals']}")
    return 0


def cmd_candidates(args):
    """Local review aid: unclassified entries from a scan report (classification null is rejected)."""
    report, problems = load_report(args.scan, SCHEMA_SCAN, SCAN_KEYS, "SCAN")
    if report is None or report["status"] != "COMPLETE":
        report_problems(problems or [problem("SCAN_INCOMPLETE", "no complete scan")])
        return 1
    print(json.dumps(sorted(report["findings"], key=entry_key), indent=2))
    return 0


def cmd_register(args):
    """Local review aid: the register rows for docs/SECURITY.md."""
    print(
        "\n".join(
            render_register(json.loads(Path(args.baseline).read_bytes())["findings"])
        )
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cov = sub.add_parser("coverage")
    cov.add_argument("--repo", required=True)
    cov.add_argument("--baseline", required=True)
    cov.add_argument("--security-doc", required=True)
    cov.add_argument("--out", required=True)
    cov.add_argument("--expect-commit", action="append", default=[])
    cov.add_argument("--expect-head")
    cov.set_defaults(func=cmd_coverage)
    scan = sub.add_parser("scan")
    scan.add_argument("--repo", required=True)
    scan.add_argument("--out", required=True)
    scan.set_defaults(func=cmd_scan)
    val = sub.add_parser("validate-baseline")
    val.add_argument("--baseline", required=True)
    val.add_argument("--security-doc", required=True)
    val.set_defaults(func=cmd_validate)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--coverage", required=True)
    ev.add_argument("--scan", required=True)
    ev.add_argument("--baseline", required=True)
    ev.add_argument("--security-doc", required=True)
    ev.add_argument("--out-dir", required=True)
    ev.set_defaults(func=evaluate)
    cand = sub.add_parser("candidates")
    cand.add_argument("--scan", required=True)
    cand.set_defaults(func=cmd_candidates)
    reg = sub.add_parser("register")
    reg.add_argument("--baseline", required=True)
    reg.set_defaults(func=cmd_register)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
