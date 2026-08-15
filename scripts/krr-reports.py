#!/usr/bin/env python3
"""
krr-reports.py - Fetch KRR pod logs, merge N runs by max values, generate markdown.

Usage:
  ./krr-reports.py
  ./krr-reports.py --skip-fetch
  ./krr-reports.py --threshold=15
  ./krr-reports.py --pods=3           # use last 3 pods (default: -1 = all)
  ./krr-reports.py --skip-fetch --threshold=5 --pods=2
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Argument parsing ──────────────────────────────────────────────────────────
SKIP_FETCH = False
THRESHOLD = 10.0
POD_COUNT = -1  # -1 = all

for _arg in sys.argv[1:]:
    if _arg == "--skip-fetch":
        SKIP_FETCH = True
    elif _arg.startswith("--threshold="):
        try:
            THRESHOLD = float(_arg.split("=", 1)[1])
        except ValueError:
            sys.exit(f"--threshold must be a number, got: {_arg}")
    elif _arg.startswith("--pods="):
        try:
            POD_COUNT = int(_arg.split("=", 1)[1])
        except ValueError:
            sys.exit(f"--pods must be an integer, got: {_arg}")
    else:
        sys.exit(f"Unknown argument: {_arg}")

if THRESHOLD <= 0:
    sys.exit(f"--threshold must be a positive number, got: {THRESHOLD}")

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
REPORT_DIR = REPO_ROOT / "resource-reports"
REPORT_DIR.mkdir(exist_ok=True)

TS = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd: list) -> str:
    """Run a subprocess and return stdout; exit with stderr on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def extract_val(v):
    """Convert a KRR value field to float or None."""
    if v is None or v == "?" or v == "null":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_nested(node, *path):
    """Safe nested dict access; returns None if any key is missing."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def max_val(a, b):
    """Return max of two nullable floats; None if both are None."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def fmt_cpu(v) -> str:
    return "—" if v is None else f"{int(v * 1000 + 0.5)}m"


def fmt_mem(v) -> str:
    return "—" if v is None else f"{int(v / 1048576 + 0.5)} MiB"


def is_off(cur, rec) -> bool:
    if cur is None or rec is None or cur == 0:
        return False
    return abs(cur - rec) / cur > THRESHOLD / 100


# ── Merge logic ───────────────────────────────────────────────────────────────
def merge_scans(all_scan_lists: list) -> list:
    """
    Merge multiple lists of KRR scan entries into one by taking the maximum
    numeric value for each (namespace, name, container) tuple.
    """
    # Maps key → dict of {field: max_value, _raw: original_scan}
    best = {}

    for scan_list in all_scan_lists:
        for scan in scan_list:
            obj = scan.get("object", {})
            key = (
                obj.get("namespace"),
                obj.get("name"),
                obj.get("container"),
            )
            cpu_req_cur = extract_val(get_nested(obj,  "allocations", "requests", "cpu"))
            mem_req_cur = extract_val(get_nested(obj,  "allocations", "requests", "memory"))
            mem_lim_cur = extract_val(get_nested(obj,  "allocations", "limits",   "memory"))
            cpu_req_rec = extract_val(get_nested(scan, "recommended", "requests", "cpu",    "value"))
            mem_req_rec = extract_val(get_nested(scan, "recommended", "requests", "memory", "value"))

            if key not in best:
                best[key] = {
                    "cpu_req_cur": cpu_req_cur,
                    "mem_req_cur": mem_req_cur,
                    "mem_lim_cur": mem_lim_cur,
                    "cpu_req_rec": cpu_req_rec,
                    "mem_req_rec": mem_req_rec,
                    "_raw": scan,
                }
            else:
                b = best[key]
                b["cpu_req_cur"] = max_val(b["cpu_req_cur"], cpu_req_cur)
                b["mem_req_cur"] = max_val(b["mem_req_cur"], mem_req_cur)
                b["mem_lim_cur"] = max_val(b["mem_lim_cur"], mem_lim_cur)
                b["cpu_req_rec"] = max_val(b["cpu_req_rec"], cpu_req_rec)
                b["mem_req_rec"] = max_val(b["mem_req_rec"], mem_req_rec)

    # Rebuild into the original KRR scan structure, patching the merged values back in
    result = []
    for key in sorted(best.keys()):
        b = best[key]
        merged = json.loads(json.dumps(b["_raw"]))  # deep copy

        obj     = merged.setdefault("object", {})
        alloc   = obj.setdefault("allocations", {})
        req     = alloc.setdefault("requests", {})
        lim     = alloc.setdefault("limits", {})
        rec     = merged.setdefault("recommended", {})
        rec_req = rec.setdefault("requests", {})

        req["cpu"]    = b["cpu_req_cur"]
        req["memory"] = b["mem_req_cur"]
        lim["memory"] = b["mem_lim_cur"]
        rec_req.setdefault("cpu",    {})["value"] = b["cpu_req_rec"]
        rec_req.setdefault("memory", {})["value"] = b["mem_req_rec"]

        result.append(merged)

    return result


# ── 1. Collect pod JSON files ─────────────────────────────────────────────────
json_files = []

if not SKIP_FETCH:
    print("Fetching KRR pod list…")
    raw_pods = run([
        "kubectl", "get", "pods", "-n", "krr", "-l", "job-name",
        "--sort-by=.metadata.creationTimestamp",
        "-o", "jsonpath={.items[*].metadata.name}",
    ])
    pods = raw_pods.strip().split() if raw_pods.strip() else []

    if not pods:
        sys.exit("No KRR pods found in namespace 'krr'")

    # Take the last N pods (most recent); -1 means all
    if POD_COUNT != -1:
        pods = pods[-POD_COUNT:]

    print(f"  using {len(pods)} pod(s): {', '.join(pods)}")

    for i, pod in enumerate(pods):
        print(f"  fetching log: {pod}")
        log = run(["kubectl", "logs", "-n", "krr", pod])
        last_line = log.strip().splitlines()[-1] if log.strip() else ""
        try:
            data = json.loads(last_line)
        except json.JSONDecodeError as e:
            sys.exit(f"Failed to parse JSON from pod {pod}: {e}")

        pod_json = REPORT_DIR / f"krr-{TS}-pod-{i + 1}.json"
        pod_json.write_text(json.dumps(data, indent=2))
        print(f"  saved → {pod_json}")
        json_files.append(pod_json)

else:
    # --skip-fetch: find existing per-pod files, then fall back to plain files
    candidates = sorted(REPORT_DIR.glob("krr-[0-9]*-pod-*.json"), reverse=True)
    if not candidates:
        candidates = sorted(REPORT_DIR.glob("krr-[0-9]*.json"), reverse=True)
    if not candidates and (REPORT_DIR / "krr.json").exists():
        candidates = [REPORT_DIR / "krr.json"]

    if not candidates:
        sys.exit(f"No KRR JSON found in {REPORT_DIR}; run without --skip-fetch first.")

    if POD_COUNT != -1:
        candidates = candidates[:POD_COUNT]

    json_files = candidates

    # Align TS to the newest file's timestamp prefix
    stem_parts = json_files[0].stem.split("-")
    if len(stem_parts) >= 3 and stem_parts[1].isdigit() and stem_parts[2].isdigit():
        TS = f"{stem_parts[1]}-{stem_parts[2]}"

    print(f"  reusing {len(json_files)} file(s):")
    for f in json_files:
        print(f"    {f}")

# ── 2. Parse all JSONs and merge ──────────────────────────────────────────────
all_scan_lists = []
first_doc = None

for jf in json_files:
    try:
        data = json.loads(Path(jf).read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Failed to parse {jf}: {e}")
    if first_doc is None:
        first_doc = data
    all_scan_lists.append(data.get("scans", []))

if not all_scan_lists:
    sys.exit("No scan data found in any JSON file.")

merged_scans = merge_scans(all_scan_lists)

# Write merged JSON (top-level structure from first doc, scans replaced)
KRR_JSON = REPORT_DIR / f"krr-{TS}.json"
merged_doc = {**first_doc, "scans": merged_scans}
KRR_JSON.write_text(json.dumps(merged_doc, indent=2))
print(f"Merged JSON  → {KRR_JSON}")

# ── 3. Generate markdown ──────────────────────────────────────────────────────
OUT = REPORT_DIR / f"krr-report-{TS}.md"

TABLE_HEADER = (
    "| Namespace | Name | Container"
    " | CPU Req (cur) | CPU Req (rec)"
    " | Mem Req (cur) | Mem Req (rec)"
    " | Mem Lim (cur) |"
)
TABLE_SEP = (
    "| --------- | ---- | ---------"
    " | :-----------: | :-----------:"
    " | :-----------: | :-----------:"
    " | :-----------: |"
)

over_rows = []
good_rows = []

for scan in merged_scans:
    obj  = scan.get("object", {})
    ns   = obj.get("namespace", "")
    name = obj.get("name", "")
    ctr  = obj.get("container", "")

    ccpu = extract_val(get_nested(obj,  "allocations", "requests", "cpu"))
    cmem = extract_val(get_nested(obj,  "allocations", "requests", "memory"))
    clim = extract_val(get_nested(obj,  "allocations", "limits",   "memory"))
    rcpu = extract_val(get_nested(scan, "recommended", "requests", "cpu",    "value"))
    rmem = extract_val(get_nested(scan, "recommended", "requests", "memory", "value"))

    row = (
        f"| {ns} | {name} | {ctr}"
        f" | {fmt_cpu(ccpu)} | {fmt_cpu(rcpu)}"
        f" | {fmt_mem(cmem)} | {fmt_mem(rmem)}"
        f" | {fmt_mem(clim)} |"
    )

    if is_off(ccpu, rcpu) or is_off(cmem, rmem):
        over_rows.append(row)
    else:
        good_rows.append(row)

thr = int(THRESHOLD) if THRESHOLD == int(THRESHOLD) else THRESHOLD
now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

lines = [
    f"# KRR Resource Report — {now_str}",
    "",
    f"## ⚠️ Out of range (>{thr} % off recommendation)",
    "",
    f"Containers whose current requests or limits deviate from the KRR recommendation by more than {thr} %.",
    "",
    TABLE_HEADER,
    TABLE_SEP,
    *sorted(over_rows),
    "",
    f"## ✅ Within range (≤{thr} % of recommendation)",
    "",
    "Containers that are already well-tuned.",
    "",
    TABLE_HEADER,
    TABLE_SEP,
    *sorted(good_rows),
    "",
]

OUT.write_text("\n".join(lines))
print(f"Written to   → {OUT}")
