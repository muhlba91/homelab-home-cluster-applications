#!/usr/bin/env bash
# Usage:
#   ./trivy-snapshot.sh              # default score threshold (7.0)
#   ./trivy-snapshot.sh 8.5          # custom threshold

set -euo pipefail

for bin in kubectl jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing dependency: $bin" >&2; exit 1; }
done

if ! kubectl get crd vulnerabilityreports.aquasecurity.github.io >/dev/null 2>&1; then
  echo "trivy-operator CRDs not found in current context ($(kubectl config current-context))" >&2
  exit 1
fi

SCORE_THRESHOLD="${1:-7.0}"
if ! [[ "$SCORE_THRESHOLD" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "invalid threshold: $SCORE_THRESHOLD (expected a number, e.g. 7.5)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$REPO_ROOT/trivy-reports"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/trivy-reports-$(date +%Y%m%d-%H%M).md"

section() { echo -e "\n## $1\n" >> "$OUT"; }

echo "# Trivy report snapshot — $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT"
echo "cluster: $(kubectl config current-context)" >> "$OUT"
echo "score threshold: $SCORE_THRESHOLD" >> "$OUT"

section "Cluster Compliance (CIS)"
kubectl get clustercompliancereports -o json \
  | jq '[.items[] | {name: .metadata.name, updated: .status.updateTimestamp, summary: .status.summary}]' >> "$OUT"

section "Cluster RBAC Assessment"
kubectl get clusterrbacassessmentreports -o json \
  | jq '[.items[] | {name: .metadata.name, summary: .report.summary}]' >> "$OUT"

section "Vulnerability Reports (summary by workload)"
kubectl get vulnerabilityreports -A -o json \
  | jq '[.items[] | {namespace: .metadata.namespace, name: .metadata.name,
         image: (.report.artifact.repository + ":" + .report.artifact.tag),
         summary: .report.summary}]' >> "$OUT"

section "Exposed Secret Reports (non-zero only)"
kubectl get exposedsecretreports -A -o json \
  | jq '[.items[] | select(.report.summary.criticalCount > 0 or .report.summary.highCount > 0)
         | {namespace: .metadata.namespace, name: .metadata.name, summary: .report.summary}]' >> "$OUT"

section "RBAC Assessment Reports (namespaced)"
kubectl get rbacassessmentreports -A -o json \
  | jq '[.items[] | {namespace: .metadata.namespace, name: .metadata.name, summary: .report.summary}]' >> "$OUT"

section "Unique findings with score >= $SCORE_THRESHOLD (deduped by CVE, workload count shown)"
kubectl get vulnerabilityreports -A -o json \
  | jq --argjson th "$SCORE_THRESHOLD" '
    [.items[] | . as $r | ($r.report.vulnerabilities // [])[]
     | select((.score // 0) >= $th)
     | {vulnerabilityID, title, severity, score, fixedVersion, resource,
        image: ($r.report.artifact.repository + ":" + $r.report.artifact.tag)}]
    | group_by(.vulnerabilityID)
    | map({
        vulnerabilityID: .[0].vulnerabilityID,
        title: .[0].title,
        severity: .[0].severity,
        score: .[0].score,
        fixedVersion: .[0].fixedVersion,
        resource: .[0].resource,
        affectedImageCount: length,
        affectedImages: ([.[].image] | unique)
      })
    | sort_by(-.score)
  ' >> "$OUT"

echo "Written to $OUT"
