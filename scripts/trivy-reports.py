#!/usr/bin/env python3
"""
scripts/trivy-reports.py

Manual/ad-hoc tool — NOT part of GitOps reconciliation.
Generates a point-in-time markdown snapshot of trivy-operator findings,
written to trivy-reports/ (gitignored) at the repo root.

Stdlib only — no pip installs required.

Usage:
    ./scripts/trivy-reports.py              # default score threshold (7.0)
    ./scripts/trivy-reports.py 8.5          # custom threshold
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_LISTED_IMAGES = 5

# Namespaces whose critical/high config-audit findings are expected by design
# (CNI, storage operator) and therefore separated into a low-priority subsection.
STRUCTURAL_NAMESPACES: frozenset[str] = frozenset({"cilium", "rook-ceph"})


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def kubectl_json(*args: str) -> dict:
    result = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        die(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def current_context() -> str:
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Compact-style markdown table (no space padding around pipes),
    consistent across header/separator/data rows — satisfies MD060."""
    if not rows:
        return "_No entries._\n"

    def esc(cell) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ")

    header_line = "|" + "|".join(esc(h) for h in headers) + "|"
    sep_line = "|" + "|".join("---" for _ in headers) + "|"
    lines = [header_line, sep_line]
    for row in rows:
        lines.append("|" + "|".join(esc(c) for c in row) + "|")
    return "\n".join(lines) + "\n"


def truncated_image_list(images: list[str]) -> str:
    if len(images) <= MAX_LISTED_IMAGES:
        return ", ".join(images)
    shown = ", ".join(images[:MAX_LISTED_IMAGES])
    return f"{shown}, +{len(images) - MAX_LISTED_IMAGES} more"


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot trivy-operator findings as markdown.")
    parser.add_argument("threshold", nargs="?", type=float, default=7.0,
                         help="minimum CVSS score for the deduped vulnerability section (default: 7.0)")
    args = parser.parse_args()

    if not shutil.which("kubectl"):
        die("missing dependency: kubectl")

    crd_check = subprocess.run(
        ["kubectl", "get", "crd", "vulnerabilityreports.aquasecurity.github.io"],
        capture_output=True, text=True, check=False,
    )
    if crd_check.returncode != 0:
        die(f"trivy-operator CRDs not found in current context ({current_context()})")

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    out_dir = repo_root / "trivy-reports"
    out_dir.mkdir(exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    out_path = out_dir / f"trivy-reports-{now_utc.strftime('%Y%m%d-%H%M')}.md"

    sections: list[str] = []

    # --- gather data ---
    compliance = kubectl_json("get", "clustercompliancereports").get("items", [])
    cluster_rbac = kubectl_json("get", "clusterrbacassessmentreports").get("items", [])
    vuln_reports = kubectl_json("get", "vulnerabilityreports", "-A").get("items", [])
    secret_reports = kubectl_json("get", "exposedsecretreports", "-A").get("items", [])
    ns_rbac = kubectl_json("get", "rbacassessmentreports", "-A").get("items", [])
    config_audit = kubectl_json("get", "configauditreports", "-A").get("items", [])

    # === 1. Compliance summary (quick glance) ===
    rows = []
    for item in compliance:
        summary = (item.get("status") or {}).get("summary")
        name = item["metadata"]["name"]
        if summary:
            rows.append([name, summary.get("passCount", "-"), summary.get("failCount", "-")])
        else:
            rows.append([name, "no data yet", "no data yet"])
    sections.append(("Compliance summary (CIS / NSA / PSS)",
                      md_table(["Framework", "Pass", "Fail"], rows)))

    # === 2. Compliance failing checks — the headline table ===
    fail_rows = []
    for item in compliance:
        framework = item["metadata"]["name"]
        checks = ((item.get("status") or {}).get("summaryReport") or {}).get("controlCheck", [])
        for check in checks:
            total_fail = check.get("totalFail", 0) or 0
            if total_fail > 0:
                fail_rows.append([framework, check.get("id", ""), check.get("severity", ""),
                                   total_fail, check.get("name", "")])
    fail_rows.sort(key=lambda r: -r[3])
    sections.append(("Failing compliance checks (sorted by count, all frameworks)",
                      md_table(["Framework", "Check ID", "Severity", "Fails", "Name"], fail_rows)))

    # === 3. Config audit — deduped by check ID (actionable namespaces only) ===
    # Structural namespaces are excluded from workload counts here so the table
    # reflects genuine remediation targets. A compact structural-only summary
    # follows for awareness.
    check_map: dict[str, dict] = {}
    check_map_structural: dict[str, dict] = {}
    for report in config_audit:
        ns = report["metadata"]["namespace"]
        name = report["metadata"]["name"]
        workload_ref = f"{ns}/{name}"
        target = check_map_structural if ns in STRUCTURAL_NAMESPACES else check_map
        for check in report.get("report", {}).get("checks", []) or []:
            if check.get("success", True):
                continue
            cid = check.get("checkID", "?")
            entry = target.setdefault(cid, {
                "title": check.get("title", ""),
                "severity": check.get("severity", ""),
                "remediation": check.get("remediation", ""),
                "workloads": set(),
            })
            entry["workloads"].add(workload_ref)

    audit_dedup_rows = []
    for cid, e in check_map.items():
        wl = sorted(e["workloads"])
        audit_dedup_rows.append([
            cid, e["severity"], len(wl), e["title"], e["remediation"],
            truncated_image_list(wl),
        ])
    audit_dedup_rows.sort(key=lambda r: -r[2])
    sections.append(("Config audit — failing checks (deduped by check ID, actionable workloads only)",
                      md_table(["Check ID", "Severity", "Affected Workloads", "Title",
                                "Remediation", "Workload examples"], audit_dedup_rows)))

    # Structural awareness table — check ID + count only, no remediation noise
    audit_structural_dedup_rows = []
    for cid, e in check_map_structural.items():
        audit_structural_dedup_rows.append([cid, e["severity"], len(e["workloads"]), e["title"]])
    audit_structural_dedup_rows.sort(key=lambda r: -r[2])
    _structural_ns_label = ", ".join(sorted(STRUCTURAL_NAMESPACES))
    sections.append((
        f"Config audit — failing checks in structural namespaces ({_structural_ns_label}) — awareness only",
        md_table(["Check ID", "Severity", "Affected Workloads", "Title"], audit_structural_dedup_rows),
    ))

    # === 4 & 5. Config audit — critical/high per workload, split actionable vs structural ===
    # Structural namespaces (cilium, rook-ceph) require privileged/hostNetwork by design;
    # their findings are expected and separated to reduce noise in the actionable table.
    audit_actionable: list[list] = []
    audit_structural: list[list] = []
    for report in config_audit:
        ns = report["metadata"]["namespace"]
        name = report["metadata"]["name"]
        for check in report.get("report", {}).get("checks", []) or []:
            if check.get("success", True):
                continue
            sev = (check.get("severity") or "").upper()
            if sev not in ("CRITICAL", "HIGH"):
                continue
            messages = "; ".join(check.get("messages", []) or [])
            row = [ns, name, sev, check.get("checkID", "?"),
                   check.get("title", ""), check.get("remediation", ""), messages]
            if ns in STRUCTURAL_NAMESPACES:
                audit_structural.append(row)
            else:
                audit_actionable.append(row)

    _sev_key = ["CRITICAL", "HIGH"].index
    audit_actionable.sort(key=lambda r: (r[0], r[1], _sev_key(r[2])))
    audit_structural.sort(key=lambda r: (r[0], r[1], _sev_key(r[2])))

    _audit_headers = ["Namespace", "Workload", "Severity", "Check ID",
                      "Title", "Remediation", "Details"]
    sections.append(("Config audit — critical/high actionable findings",
                      md_table(_audit_headers, audit_actionable)))
    sections.append((
        f"Config audit — critical/high structural findings ({_structural_ns_label} — expected by design)",
        md_table(_audit_headers, audit_structural),
    ))

    # === 6. Deduped vulnerability findings above threshold ===
    cve_map: dict[str, dict] = {}
    for report in vuln_reports:
        artifact = report.get("report", {}).get("artifact", {})
        image = f"{artifact.get('repository', '?')}:{artifact.get('tag', '?')}"
        for vuln in report.get("report", {}).get("vulnerabilities", []) or []:
            if (vuln.get("score") or 0) < args.threshold:
                continue
            vid = vuln.get("vulnerabilityID", "?")
            entry = cve_map.setdefault(vid, {
                "title": vuln.get("title", ""),
                "severity": vuln.get("severity", ""),
                "score": vuln.get("score", 0),
                "fixedVersion": vuln.get("fixedVersion", ""),
                "resource": vuln.get("resource", ""),
                "images": set(),
            })
            entry["images"].add(image)

    vuln_rows = []
    for vid, e in cve_map.items():
        images = sorted(e["images"])
        vuln_rows.append([vid, e["title"], e["severity"], e["score"], e["fixedVersion"],
                           e["resource"], len(images), truncated_image_list(images)])
    vuln_rows.sort(key=lambda r: -(r[3] or 0))
    sections.append((f"Unique vulnerability findings — score >= {args.threshold} (deduped by CVE)",
                      md_table(["CVE", "Title", "Severity", "Score", "Fixed Version",
                                "Resource", "Affected Images", "Examples"], vuln_rows)))

    # === 7. Exposed secrets (non-zero only) ===
    secret_rows = []
    for item in secret_reports:
        summary = item.get("report", {}).get("summary", {})
        if summary.get("criticalCount", 0) > 0 or summary.get("highCount", 0) > 0:
            secret_rows.append([item["metadata"]["namespace"], item["metadata"]["name"],
                                 summary.get("criticalCount", 0), summary.get("highCount", 0),
                                 summary.get("mediumCount", 0), summary.get("lowCount", 0)])
    secret_rows.sort(key=lambda r: (-r[2], -r[3]))
    sections.append(("Exposed secret reports (critical/high only)",
                      md_table(["Namespace", "Name", "Critical", "High", "Medium", "Low"], secret_rows)))

    # === 8. RBAC — cluster scoped ===
    rbac_cluster_rows = []
    for item in cluster_rbac:
        s = item.get("report", {}).get("summary", {})
        rbac_cluster_rows.append([item["metadata"]["name"], s.get("criticalCount", 0),
                                   s.get("highCount", 0), s.get("mediumCount", 0), s.get("lowCount", 0)])
    rbac_cluster_rows.sort(key=lambda r: (-r[1], -r[2]))
    sections.append(("RBAC assessment — cluster scoped",
                      md_table(["Name", "Critical", "High", "Medium", "Low"], rbac_cluster_rows)))

    # === 9. RBAC — namespaced ===
    rbac_ns_rows = []
    for item in ns_rbac:
        s = item.get("report", {}).get("summary", {})
        rbac_ns_rows.append([item["metadata"]["namespace"], item["metadata"]["name"],
                              s.get("criticalCount", 0), s.get("highCount", 0),
                              s.get("mediumCount", 0), s.get("lowCount", 0)])
    rbac_ns_rows.sort(key=lambda r: (-r[2], -r[3]))
    sections.append(("RBAC assessment — namespaced",
                      md_table(["Namespace", "Name", "Critical", "High", "Medium", "Low"], rbac_ns_rows)))

    # === 10. Vulnerability reports summary by workload (long tail reference) ===
    vr_rows = []
    for report in vuln_reports:
        artifact = report.get("report", {}).get("artifact", {})
        image = f"{artifact.get('repository', '?')}:{artifact.get('tag', '?')}"
        s = report.get("report", {}).get("summary", {})
        vr_rows.append([report["metadata"]["namespace"], report["metadata"]["name"], image,
                         s.get("criticalCount", 0), s.get("highCount", 0),
                         s.get("mediumCount", 0), s.get("lowCount", 0), s.get("unknownCount", 0)])
    vr_rows.sort(key=lambda r: (-r[3], -r[4]))
    sections.append(("Vulnerability reports — summary by workload",
                      md_table(["Namespace", "Workload", "Image", "Critical", "High",
                                "Medium", "Low", "Unknown"], vr_rows)))

    # --- write file, most important sections first ---
    with out_path.open("w") as f:
        f.write(f"# Trivy report snapshot — {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n")
        f.write(f"cluster: {current_context()}\n")
        f.write(f"score threshold: {args.threshold}\n")
        for title, body in sections:
            f.write(f"\n## {title}\n\n{body}")

    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
