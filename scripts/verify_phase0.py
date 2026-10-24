"""Phase 0 documentation consistency check for LLM Insight.

Read-only, standard library only. Run from anywhere:

    python -I scripts/verify_phase0.py           # every check
    python -I scripts/verify_phase0.py --quiet   # failures + summary only

Exit code 0 when every check passes, 1 otherwise.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
QUIET = "--quiet" in sys.argv[1:]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")  # Windows consoles may lack characters such as §

SOURCE_DOCS = {"docs/MVP_MASTER_PLAN.md", "docs/CLAUDE_PHASE_PROMPTS.md"}
REQUIRED = [
    "CLAUDE.md", "README_START_HERE.md", ".gitignore", ".env.example", "scripts/verify_phase0.py",
    "docs/MVP_MASTER_PLAN.md", "docs/CLAUDE_PHASE_PROMPTS.md",
    "docs/PRD.md", "docs/ADR.md", "docs/ARCHITECTURE.md", "docs/TELEMETRY_CONTRACT.md",
    "docs/METRICS_CATALOG.md", "docs/SECURITY.md", "docs/TEST_MATRIX.md", "docs/DEMO_SCRIPT.md",
    "docs/PHASE_STATUS.md", "docs/SOURCES_AND_RISKS.md", "docs/reports/phase-0.md",
    "docs/fixtures/otlp_trace_3span.json", "docs/fixtures/README.md",
]
PROMOTED_CRITICAL = {"T15", "T16", "T44"}  # ADR-20
EXPECTED = {"tests": 48, "critical": 42, "blockers": 10}

failures = []
passes = 0


def check(cond, msg):
    global passes
    if cond:
        passes += 1
        if not QUIET:
            print("PASS " + msg)
    else:
        failures.append(msg)
        print("FAIL " + msg)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, start, end):
    i = text.index(start)
    j = text.index(end, i + len(start)) if end else len(text)
    return text[i:j]


def table_rows(block):
    """Markdown table body rows as lists of stripped cells (header and separator excluded)."""
    rows = [ln for ln in block.splitlines() if ln.startswith("|")]
    return [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]]


# ---------------------------------------------------------------- 1. files
missing = [p for p in REQUIRED if not os.path.isfile(os.path.join(ROOT, p))]
check(not missing, f"required files present ({len(REQUIRED) - len(missing)}/{len(REQUIRED)}) missing={missing}")
if missing:
    print(f"\nSUMMARY: {passes} PASS, {len(failures)} FAIL (stopped: missing files)")
    sys.exit(1)

own_docs = sorted(
    os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/")
    for dp, _, fs in os.walk(os.path.join(ROOT, "docs")) for f in fs if f.endswith(".md")
)
# Contract docs: everything we maintain except source docs and the historical report.
contract_docs = [p for p in own_docs if p not in SOURCE_DOCS and not p.startswith("docs/reports/")] + ["CLAUDE.md"]
text = {p: read(p) for p in contract_docs}

# ---------------------------------------------------------------- 2. test matrix
tm = read("docs/TEST_MATRIX.md")
main_table = section(tm, "| ID | Group |", "**Counts:**")
rows = table_rows(main_table)
ids = [r[0] for r in rows]
check(len(ids) == EXPECTED["tests"] and len(set(ids)) == len(ids), f"TEST_MATRIX rows={len(ids)} unique={len(set(ids))}")
check(ids == [f"T{i:02d}" for i in range(1, 49)], "TEST_MATRIX IDs are T01..T48 in order")
crit = {r[0] for r in rows if "★" in r[4]}
block = {r[0] for r in rows if "⛔" in r[5]}
check(len(crit) == EXPECTED["critical"], f"critical count = {len(crit)} (expected {EXPECTED['critical']})")
check(len(block) == EXPECTED["blockers"], f"blocker count = {len(block)} (expected {EXPECTED['blockers']})")
check(block <= crit, "all presentation blockers are critical (subset)")
# A result may be claimed only as "PASS|PARTIAL|FAIL (phase-N)", and docs/reports/phase-N.md must
# exist and name the test ID (Phase 1B+); everything else stays NOT RUN.
for r in rows:
    if r[8] == "NOT RUN":
        continue
    m = re.fullmatch(r"(PASS|PARTIAL|FAIL) \(phase-([0-9a-z]+)\)", r[8])
    report = os.path.join(ROOT, "docs", "reports", f"phase-{m.group(2)}.md") if m else None
    ok = bool(m) and os.path.isfile(report) and re.search(rf"\b{r[0]}\b", read(os.path.relpath(report, ROOT)))
    check(bool(ok), f"{r[0]} status '{r[8]}' is NOT RUN or backed by a phase report naming it")

mp = read("docs/MVP_MASTER_PLAN.md")
sec_i = section(mp, "## I.", "## J.")
mp_ids = set(re.findall(r"\b(T\d\d)\*?", sec_i))
mp_star = set(re.findall(r"\b(T\d\d)\*", sec_i))
mp_block = set(re.findall(r"T\d\d", re.search(r"(T01/T04/[T0-9/]+)", sec_i).group(1)))
check(mp_ids == set(ids), f"matrix IDs == master plan IDs ({len(mp_ids)})")
check(len(mp_star) == 39 and crit == mp_star | PROMOTED_CRITICAL,
      f"critical == master plan starred ({len(mp_star)}) + ADR-20 promoted {sorted(PROMOTED_CRITICAL)}")
check(mp_block == block, f"blockers == master plan blocker list {sorted(mp_block)}")
counts_line = section(tm, "**Counts:**", "## Sub-cases")
check(f"{EXPECTED['critical']} critical" in counts_line and f"{EXPECTED['blockers']} blockers" in counts_line,
      "TEST_MATRIX counts text matches computed counts")
sub_rows = table_rows(section(tm, "## Sub-cases", "**Benchmark"))
check(all(r[1] in ids and r[0].startswith(r[1] + "-S") for r in sub_rows),
      f"all {len(sub_rows)} sub-cases reference an existing parent ID")
for p, t in text.items():
    if p == "docs/TEST_MATRIX.md":
        continue
    # "At least 24 critical" is the master plan's minimum floor, not a count of defined tests.
    stale = re.findall(r"(?<![Aa]t least )\b(\d+) critical\b", t)
    check(all(n == str(EXPECTED["critical"]) for n in stale), f"{p}: critical count mentions {stale or '-'} are {EXPECTED['critical']}")

# ---------------------------------------------------------------- 3. ADR identifiers and cross-references
adr = text["docs/ADR.md"]
adr_ids = set(re.findall(r"^\| (ADR-\d\d|CR-\d\d) \|", adr, re.M)) | set(re.findall(r"^### (ADR-\d\d) ", adr, re.M))
check(adr_ids >= {f"ADR-{i:02d}" for i in range(1, 22)}, "ADR-01..ADR-21 defined")
check(adr_ids >= {f"CR-{i:02d}" for i in range(1, 10)}, "CR-01..CR-09 defined")
check("Proposed" not in section(adr, "## 3.", None) and "pending" not in adr.lower(), "ADR.md has no open proposals")
metrics_ids = set(re.findall(r"^\| (M\d\d) \|", text["docs/METRICS_CATALOG.md"], re.M))
risk_ids = set(re.findall(r"^\| (R\d\d) \|", text["docs/SOURCES_AND_RISKS.md"], re.M))
for p, t in text.items():
    for ln_no, line in enumerate(t.splitlines(), 1):
        for m in re.finditer(r"PADR-\d\d", line):
            check("formerly PADR" in line or "proposed as PADR" in line, f"{p}:{ln_no} {m.group(0)} only as former-name mapping")
    refs = set(re.findall(r"\b((?:ADR|CR)-\d\d)\b", t))
    check(refs <= adr_ids, f"{p}: ADR/CR refs resolve (unresolved={sorted(refs - adr_ids)})")
    trefs = {r for r in re.findall(r"\b(T\d\d)\b", t)}
    check(trefs <= set(ids), f"{p}: test refs resolve (unresolved={sorted(trefs - set(ids))})")
    mrefs = set(re.findall(r"\b(M\d\d)\b", t))
    check(mrefs <= metrics_ids, f"{p}: metric refs resolve (unresolved={sorted(mrefs - metrics_ids)})")
    rrefs = set(re.findall(r"\b(R\d\d)\b", t))
    check(rrefs <= risk_ids, f"{p}: risk refs resolve (unresolved={sorted(rrefs - risk_ids)})")
    for ref in set(re.findall(r"`(?:docs/)?([A-Z_]+\.md)`", t)):
        if ref in ("FINAL_DELIVERY_REPORT.md", "README.md"):
            continue  # planned for Phase 5
        ok = os.path.isfile(os.path.join(ROOT, "docs", ref)) or os.path.isfile(os.path.join(ROOT, ref))
        check(ok, f"{p}: doc reference {ref} exists")

# ---------------------------------------------------------------- 4. naming consistency
VOCAB = {
    "cost_status": {"computed", "unknown"},
    "call_key_source": {"provider_response_id", "sdk_attempt", "span_fallback"},
    "cost_unknown_reason": {"price_missing", "usage_missing", "usage_inconsistent"},
    "usage_source": {"provider_reported", "estimated", "unknown"},
    "review_status": {"open", "in_review", "closed"},
    "reviewer_decision": {"pending", "hypothesis_supported", "hypothesis_rejected", "inconclusive"},
    "provenance": {"real", "synthetic", "mixed", "unknown"},
}
for p, t in text.items():
    for field, allowed in VOCAB.items():
        used = set(re.findall(rf"\b{field}\s*=\s*`?'?([a-z_]+)", t))
        check(used <= allowed, f"{p}: {field} values {sorted(used) or '-'} within vocabulary")
    for ln_no, line in enumerate(t.splitlines(), 1):
        if re.search(r"(?<!server_)dropped_attributes_count", line):
            check("client" in line.lower() or "OTLP" in line, f"{p}:{ln_no} bare dropped_attributes_count only for the client/OTLP field")
        for legacy in ("gen_ai.system`", "gen_ai.client.operation.duration", "prompt_tokens", "completion_tokens"):
            if legacy in line:
                ok = any(k in line.lower() for k in ("alias", "absent", "not used", "legacy", "no longer"))
                check(ok, f"{p}:{ln_no} legacy name {legacy.strip('`')} only as alias/legacy")

arch = text["docs/ARCHITECTURE.md"]
fr = section(arch, "### 4.9 `failure_reviews`", "### 4.10")
for f in ("issue_type", "evidence", "hypothesis", "review_status", "reviewer_decision"):
    check(f"| {f} |" in fr, f"failure_reviews defines {f}")
for v in ("retrieval_missing", "chunk_boundary", "prompt_instruction", "generation_mismatch", "schema_violation", "unknown"):
    check(f"`{v}`" in fr, f"issue_type enum value {v} documented")

# ---------------------------------------------------------------- 5. database contract completeness (ADR-21)
db = section(arch, "## 4. Database contract", "### 4.13")
tables = {}
for m in re.finditer(r"^### 4\.\d+ `(\w+)`(.*?)(?=^### |\Z)", db, re.M | re.S):
    tables[m.group(1)] = {r[0]: r for r in table_rows(m.group(2)) if len(r) == 6}
mp_e = section(mp, "## E.", "**Indeksler")
RENAME = {"failure_reviews": {"category": "issue_type", "status": "review_status"}}
SPLIT = {"start/end_ns": ["start_ns", "end_ns"], "input/output_tokens": ["input_tokens", "output_tokens"],
         "whitelisted JSONB": ["attributes"], "mode(real/mock)": ["mode"]}
for m in re.finditer(r"^\| `(\w+)` \| ([^|]+) \|", mp_e, re.M):
    tname, fields = m.group(1), []
    for raw in m.group(2).split(","):
        name = raw.strip().replace(" nullable", "")
        fields += SPLIT.get(name, [RENAME.get(tname, {}).get(name, name)])
    cols = tables.get(tname, {})
    check(bool(cols), f"DB contract defines master-plan table {tname}")
    lost = [f for f in fields if f not in cols]
    check(not lost, f"{tname}: every master plan §E field present (missing={lost})")
    for col, row in cols.items():
        origin = row[5]
        check(re.match(r"^(§E|CR-03|ADD: .+)", origin) is not None, f"{tname}.{col}: origin is §E / CR-03 / 'ADD: reason' ({origin[:30]})")
        if origin.startswith("§E") and col != "id" and RENAME.get(tname, {}).get(col) is None:
            check(col in fields, f"{tname}.{col}: marked §E and really in master plan §E")
        check(row[2] in ("PK", "NULL", "NOT NULL"), f"{tname}.{col}: nullability stated ({row[2]})")
    if "id" in cols:
        check(cols["id"][1] == "BIGSERIAL" and cols["id"][2] == "PK", f"{tname}.id is BIGSERIAL PK")
check("server_dropped_attributes_count" in tables.get("spans", {}), "spans.server_dropped_attributes_count defined (ADR-21 rename)")
check(not any(c == "dropped_attributes_count" for t in tables.values() for c in t), "no bare dropped_attributes_count column")
ext = [(t, c) for t, cols in tables.items() for c, r in cols.items() if c in ("trace_id", "span_id", "origin_trace_id", "parent_span_id")]
check(all(tables[t][c][1] == "TEXT" for t, c in ext), f"OTel IDs stored as TEXT external identifiers ({len(ext)} columns)")

# ---------------------------------------------------------------- 6. fixture integrity
fx = json.loads(read("docs/fixtures/otlp_trace_3span.json"))
spans = [s for rs in fx["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]
check(len(spans) == 3, f"fixture span count = {len(spans)}")
check(len({s["traceId"] for s in spans}) == 1, "fixture is a single trace")
check(all(re.fullmatch(r"[0-9a-f]{32}", s["traceId"]) for s in spans), "fixture traceId is 32 lowercase hex")
check(all(re.fullmatch(r"[0-9a-f]{16}", s["spanId"]) for s in spans), "fixture spanId is 16 lowercase hex")
sid = {s["spanId"] for s in spans}
check(all(s.get("parentSpanId", "") in sid | {""} for s in spans), "fixture parent references resolve")
check(all(int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"]) > 0 for s in spans), "fixture end >= start > 0")
check(sorted(s["kind"] for s in spans) == [1, 2, 3], "fixture kinds INTERNAL/SERVER/CLIENT")
llm = next(s for s in spans if s["kind"] == 3)
attrs = {a["key"]: a["value"] for a in llm["attributes"]}
check({"gen_ai.operation.name", "gen_ai.provider.name"} <= set(attrs), "CLIENT span has semconv-required attributes")
check(llm["name"] == f"{attrs['gen_ai.operation.name']['stringValue']} {attrs['gen_ai.request.model']['stringValue']}",
      "CLIENT span name follows '{operation} {model}' (ADR-13)")
check(not {"gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions"} & set(attrs), "fixture has no content attributes")
check(attrs.get("llminsight.provenance", {}).get("stringValue") == "synthetic", "fixture labeled synthetic")
fx_readme = read("docs/fixtures/README.md")
check(f"resp:openai:{attrs['gen_ai.response.id']['stringValue']}" in fx_readme and "provider_response_id" in fx_readme,
      "fixture expected key uses response-ID precedence")

# ---------------------------------------------------------------- 7. CLAUDE.md size
n = len(read("CLAUDE.md").splitlines())
check(n <= 60, f"CLAUDE.md lines = {n} (<= 60)")

# ---------------------------------------------------------------- 8. secrets and placeholders
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|sk-proj-|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|lli_[A-Za-z0-9]{6,}_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}\.)")
hits, scanned = [], 0
for dp, dn, fs in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in (".git", "node_modules", ".venv", "venv")]
    for f in fs:
        fp = os.path.join(dp, f)
        if os.path.abspath(fp) == os.path.abspath(__file__):
            continue  # this file contains the patterns themselves
        if os.path.abspath(fp) == ENV_PATH:
            continue  # local secrets by design; checked below to be git-ignored and untracked
        try:
            t = open(fp, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        hits += [(os.path.relpath(fp, ROOT), m.group(0)[:8] + "...") for m in SECRET.finditer(t)]
check(not hits, f"secret-pattern scan over {scanned} files: hits={hits}")
if os.path.exists(ENV_PATH):
    # Phase 1A+: a local .env (scripts/dev/init_env.py) is expected; it must never be committable.
    def git(*args):
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True).returncode

    ignored = git("check-ignore", "-q", ".env") == 0
    tracked = git("ls-files", "--error-unmatch", ".env") == 0
    check(ignored and not tracked, f"local .env is git-ignored ({ignored}) and untracked ({not tracked})")
else:
    check(True, "no .env file in the working tree")
env = read(".env.example")
env_pairs = [ln.split("=", 1) for ln in env.splitlines() if "=" in ln and not ln.lstrip().startswith("#")]
bad = [k for k, v in env_pairs
       if any(s in k for s in ("PASSWORD", "PEPPER", "TOKEN", "API_KEY", "HEADERS"))
       and v not in ("", "CHANGE_ME", "X-Project-Key=CHANGE_ME")]
bad += [k for k, v in env_pairs if "DATABASE_URL" in k and ":CHANGE_ME@" not in v]
check(not bad, f".env.example secret-bearing keys are placeholders ({len(env_pairs)} keys, bad={bad})")
check(not any(k.startswith("NEXT_PUBLIC_") for k, _ in env_pairs), ".env.example defines no NEXT_PUBLIC_ variables")
for p in contract_docs + ["docs/reports/phase-0.md"]:
    check("PLACEHOLDER" not in read(p), f"{p}: no unfilled PLACEHOLDER markers")

print(f"\nSUMMARY: {passes} PASS, {len(failures)} FAIL")
sys.exit(1 if failures else 0)
