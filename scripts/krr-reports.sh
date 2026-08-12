#!/usr/bin/env bash
# Usage:
#   ./krr-reports.sh          # fetch latest KRR log, analyse and write markdown
#   ./krr-reports.sh --skip-fetch   # skip kubectl fetch, analyse existing resource-reports/krr.json

set -euo pipefail

for bin in kubectl jq awk; do
  command -v "$bin" >/dev/null 2>&1 || { echo "missing dependency: $bin" >&2; exit 1; }
done

SKIP_FETCH=false
if [[ "${1:-}" == "--skip-fetch" ]]; then
  SKIP_FETCH=true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_DIR="$REPO_ROOT/resource-reports"
mkdir -p "$REPORT_DIR"

TS="$(date +%Y%m%d-%H%M)"
KRR_JSON="$REPORT_DIR/krr-$TS.json"
OUT="$REPORT_DIR/krr-report-$TS.md"

# ── 1. Fetch latest KRR job log ──────────────────────────────────────────────
if [[ "$SKIP_FETCH" == false ]]; then
  echo "Fetching latest KRR pod log…"
  LATEST_POD=$(kubectl get pods -n krr -l job-name \
    --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{.items[-1].metadata.name}')
  if [[ -z "$LATEST_POD" ]]; then
    echo "No KRR pod found in namespace 'krr'" >&2
    exit 1
  fi
  echo "  pod: $LATEST_POD"
  kubectl logs -n krr "$LATEST_POD" | tail -n1 | jq . > "$KRR_JSON"
  echo "  saved → $KRR_JSON"
else
  # --skip-fetch: prefer newest timestamped krr-YYYYMMDD-HHMM.json,
  # fall back to the legacy krr.json
  KRR_JSON="$(find "$REPORT_DIR" -maxdepth 1 -name 'krr-[0-9]*.json' -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null | head -1 || true)"
  if [[ -z "$KRR_JSON" ]] && [[ -f "$REPORT_DIR/krr.json" ]]; then
    KRR_JSON="$REPORT_DIR/krr.json"
  fi
  if [[ -z "$KRR_JSON" ]]; then
    echo "No krr JSON found in $REPORT_DIR; run without --skip-fetch first." >&2
    exit 1
  fi
  echo "  reusing → $KRR_JSON"
  # realign the output timestamp to match the json file (keep TS from date above for legacy file)
  BASE="$(basename "$KRR_JSON" .json)"
  if [[ "$BASE" =~ ^krr-[0-9]{8}-[0-9]{4}$ ]]; then
    TS="${BASE#krr-}"
    OUT="$REPORT_DIR/krr-report-$TS.md"
  fi
fi

# ── 2. Extract rows as TSV via jq ────────────────────────────────────────────
# Columns (raw, nulls become the string "null"):
#   namespace | name | container
#   cpu_req_cur (cores) | cpu_req_rec (cores)
#   mem_req_cur (bytes) | mem_req_rec (bytes)
#   mem_lim_cur (bytes)
TSV=$(jq -r '
  .scans[] |
  [
    .object.namespace,
    .object.name,
    .object.container,
    (if .object.allocations.requests.cpu == null or
        .object.allocations.requests.cpu == "?" then "null"
     else (.object.allocations.requests.cpu | tostring) end),
    (if .recommended.requests.cpu.value == null or
        .recommended.requests.cpu.value == "?" then "null"
     else (.recommended.requests.cpu.value | tostring) end),
    (if .object.allocations.requests.memory == null or
        .object.allocations.requests.memory == "?" then "null"
     else (.object.allocations.requests.memory | tostring) end),
    (if .recommended.requests.memory.value == null or
        .recommended.requests.memory.value == "?" then "null"
     else (.recommended.requests.memory.value | tostring) end),
    (if .object.allocations.limits.memory == null or
        .object.allocations.limits.memory == "?" then "null"
     else (.object.allocations.limits.memory | tostring) end)
  ] | @tsv
' "$KRR_JSON")

# ── 3. Build markdown ────────────────────────────────────────────────────────
TABLE_HEADER="| Namespace | Name | Container | CPU Req (cur) | CPU Req (rec) | Mem Req (cur) | Mem Req (rec) | Mem Lim (cur) |"
TABLE_SEP="| --------- | ---- | --------- | :-----------: | :-----------: | :-----------: | :-----------: | :-----------: |"

# Split rows into two files, then assemble the report
OVER_FILE=$(mktemp)
GOOD_FILE=$(mktemp)
trap 'rm -f "$OVER_FILE" "$GOOD_FILE"' EXIT

echo "$TSV" | awk -F'\t' -v over="$OVER_FILE" -v good="$GOOD_FILE" '
  function fmt_cpu(v,    m) {
    if (v == "null") return "—"
    m = int(v * 1000 + 0.5)
    return m "m"
  }
  function fmt_mem(v,    mib) {
    if (v == "null") return "—"
    mib = int(v / 1048576 + 0.5)
    return mib " MiB"
  }
  function off(cur, rec,    ratio) {
    if (cur == "null" || rec == "null") return 0
    if (cur + 0 == 0) return 0
    ratio = (cur - rec) / cur
    if (ratio < 0) ratio = -ratio
    return (ratio > 0.10)
  }
  {
    ns   = $1; name = $2; ctr = $3
    ccpu = $4; rcpu = $5
    cmem = $6; rmem = $7
    clim = $8

    row = "| " ns " | " name " | " ctr \
        " | " fmt_cpu(ccpu) " | " fmt_cpu(rcpu) \
        " | " fmt_mem(cmem) " | " fmt_mem(rmem) \
        " | " fmt_mem(clim) " |"

    if (off(ccpu, rcpu) || off(cmem, rmem))
      print row >> over
    else
      print row >> good
  }'

{
  echo "# KRR Resource Report — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo ""

  echo "## ⚠️ Out of range (>10 % off recommendation)"
  echo ""
  echo "Containers whose current requests or limits deviate from the KRR recommendation by more than 10 %."
  echo ""
  echo "$TABLE_HEADER"
  echo "$TABLE_SEP"
  sort "$OVER_FILE"
  echo ""

  echo "## ✅ Within range (≤10 % of recommendation)"
  echo ""
  echo "Containers that are already well-tuned."
  echo ""
  echo "$TABLE_HEADER"
  echo "$TABLE_SEP"
  sort "$GOOD_FILE"

} > "$OUT"

echo "Written to $OUT"
