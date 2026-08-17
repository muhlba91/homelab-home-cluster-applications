#!/usr/bin/env python3
"""
scripts/krr-reports.py

Manual/ad-hoc tool — NOT part of GitOps reconciliation.
Fetches KRR pod logs, merges N runs by taking the maximum observed value
for each container, and generates a Markdown report.

Stdlib only — no pip installs required.

Usage:
    ./scripts/krr-reports.py                              # fetch all pods, 10 % threshold
    ./scripts/krr-reports.py --skip-fetch                 # reuse existing JSON files
    ./scripts/krr-reports.py --threshold=15               # stricter threshold
    ./scripts/krr-reports.py --pods=3                     # use only last 3 pods
    ./scripts/krr-reports.py --skip-fetch --threshold=5 --pods=2
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Helpers ───────────────────────────────────────────────────────────────────

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str]) -> str:
    """Run a subprocess and return stdout; die with stderr on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def extract_val(v) -> float | None:
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


def max_val(a: float | None, b: float | None) -> float | None:
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


def is_off(cur: float | None, rec: float | None, threshold: float) -> bool:
    if cur is None or rec is None or cur == 0:
        return False
    return abs(cur - rec) / cur > threshold / 100


# ── Merge logic ───────────────────────────────────────────────────────────────

def merge_scans(all_scan_lists: list) -> list:
    """
    Merge multiple lists of KRR scan entries into one by taking the maximum
    numeric value for each (namespace, name, container) tuple.
    """
    best: dict = {}

    for scan_list in all_scan_lists:
        for scan in scan_list:
            obj = scan.get("object", {})
            key = (obj.get("namespace"), obj.get("name"), obj.get("container"))
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

    # Rebuild into the original KRR scan structure, patching merged values back in
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Merge KRR pod logs and generate a Markdown report.")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="reuse existing JSON files instead of hitting the cluster")
    parser.add_argument("--threshold", type=float, default=10.0,
                        help="deviation threshold in %% before a container is flagged (default: 10)")
    parser.add_argument("--pods", type=int, default=-1,
                        help="max number of most-recent KRR pods to use (-1 = all, default: -1)")
    args = parser.parse_args()

    if args.threshold <= 0:
        die(f"--threshold must be a positive number, got: {args.threshold}")

    if not shutil.which("kubectl"):
        die("missing dependency: kubectl")

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    report_dir = repo_root / "resource-reports"
    report_dir.mkdir(exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y%m%d-%H%M")

    # ── 1. Collect pod JSON files ─────────────────────────────────────────────

    json_files: list[Path] = []

    if not args.skip_fetch:
        print("Fetching KRR pod list…")
        raw_pods = run([
            "kubectl", "get", "pods", "-n", "krr", "-l", "job-name",
            "--sort-by=.metadata.creationTimestamp",
            "-o", "jsonpath={.items[*].metadata.name}",
        ])
        pods = raw_pods.strip().split() if raw_pods.strip() else []

        if not pods:
            die("No KRR pods found in namespace 'krr'")

        if args.pods != -1:
            pods = pods[-args.pods:]

        print(f"  using {len(pods)} pod(s): {', '.join(pods)}")

        for i, pod in enumerate(pods):
            print(f"  fetching log: {pod}")
            log = run(["kubectl", "logs", "-n", "krr", pod])
            last_line = log.strip().splitlines()[-1] if log.strip() else ""
            try:
                data = json.loads(last_line)
            except json.JSONDecodeError as e:
                die(f"Failed to parse JSON from pod {pod}: {e}")

            pod_json = report_dir / f"krr-{ts}-pod-{i + 1}.json"
            pod_json.write_text(json.dumps(data, indent=2))
            print(f"  saved → {pod_json}")
            json_files.append(pod_json)

    else:
        # --skip-fetch: find existing per-pod files, then fall back to plain files
        candidates = sorted(report_dir.glob("krr-[0-9]*-pod-*.json"), reverse=True)
        if not candidates:
            candidates = sorted(report_dir.glob("krr-[0-9]*.json"), reverse=True)
        if not candidates and (report_dir / "krr.json").exists():
            candidates = [report_dir / "krr.json"]

        if not candidates:
            die(f"No KRR JSON found in {report_dir}; run without --skip-fetch first.")

        if args.pods != -1:
            candidates = candidates[:args.pods]

        json_files = candidates

        # Align ts to the newest file's timestamp prefix
        stem_parts = json_files[0].stem.split("-")
        if len(stem_parts) >= 3 and stem_parts[1].isdigit() and stem_parts[2].isdigit():
            ts = f"{stem_parts[1]}-{stem_parts[2]}"

        print(f"  reusing {len(json_files)} file(s):")
        for f in json_files:
            print(f"    {f}")

    # ── 2. Parse all JSONs and merge ──────────────────────────────────────────

    all_scan_lists: list = []
    first_doc = None

    for jf in json_files:
        try:
            data = json.loads(Path(jf).read_text())
        except json.JSONDecodeError as e:
            die(f"Failed to parse {jf}: {e}")
        if first_doc is None:
            first_doc = data
        all_scan_lists.append(data.get("scans", []))

    if not all_scan_lists:
        die("No scan data found in any JSON file.")

    merged_scans = merge_scans(all_scan_lists)

    # Write merged JSON (top-level structure from first doc, scans replaced)
    krr_json = report_dir / f"krr-{ts}.json"
    merged_doc = {**first_doc, "scans": merged_scans}
    krr_json.write_text(json.dumps(merged_doc, indent=2))
    print(f"Merged JSON  → {krr_json}")

    # ── 3. Generate markdown ──────────────────────────────────────────────────

    out_path = report_dir / f"krr-report-{ts}.md"

    table_header = (
        "| Namespace | Name | Container"
        " | CPU Req (cur) | CPU Req (rec)"
        " | Mem Req (cur) | Mem Req (rec)"
        " | Mem Lim (cur) |"
    )
    table_sep = (
        "| --------- | ---- | ---------"
        " | :-----------: | :-----------:"
        " | :-----------: | :-----------:"
        " | :-----------: |"
    )

    # Each entry: (row_str, ccpu, cmem, rcpu, rmem)
    over_rows: list[tuple] = []
    good_rows: list[tuple] = []

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

        entry = (row, ccpu, cmem, rcpu, rmem)
        if is_off(ccpu, rcpu, args.threshold) or is_off(cmem, rmem, args.threshold):
            over_rows.append(entry)
        else:
            good_rows.append(entry)

    def diff_summary(rows: list[tuple]) -> list[str]:
        """Return summary lines for CPU and memory differences after a table."""
        total_cpu_diff = sum((r or 0) - (c or 0) for _, c, _, r, _ in rows)
        total_mem_diff = sum((r or 0) - (c or 0) for _, _, c, _, r in rows)

        def fmt_diff_cpu(v: float) -> str:
            sign = "+" if v >= 0 else ""
            return f"{sign}{int(v * 1000 + (0.5 if v >= 0 else -0.5))}m"

        def fmt_diff_mem(v: float) -> str:
            sign = "+" if v >= 0 else ""
            return f"{sign}{int(v / 1048576 + (0.5 if v >= 0 else -0.5))} MiB"

        return [
            "",
            f"**CPU difference (rec − cur):** {fmt_diff_cpu(total_cpu_diff)}  ",
            f"**Memory difference (rec − cur):** {fmt_diff_mem(total_mem_diff)}",
        ]

    thr = int(args.threshold) if args.threshold == int(args.threshold) else args.threshold
    now_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"# KRR Resource Report — {now_str}",
        "",
        f"## ⚠️ Out of range (>{thr} % off recommendation)",
        "",
        f"Containers whose current requests or limits deviate from the KRR recommendation by more than {thr} %.",
        "",
        table_header,
        table_sep,
        *sorted(r for r, *_ in over_rows),
        *diff_summary(over_rows),
        "",
        f"## ✅ Within range (≤{thr} % of recommendation)",
        "",
        "Containers that are already well-tuned.",
        "",
        table_header,
        table_sep,
        *sorted(r for r, *_ in good_rows),
        *diff_summary(good_rows),
        "",
    ]

    out_path.write_text("\n".join(lines))
    print(f"Written to   → {out_path}")


if __name__ == "__main__":
    main()
