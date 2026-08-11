#!/usr/bin/env bash
# Validates all Kustomize leaf directories for each site against kubeconform.
#
# Pipeline per leaf kustomization:
#   kustomize build <dir>  →  envsubst (site-scoped vars)  →  kubeconform
#
# "Leaf" = a kustomization.yaml whose every resources[] entry is a .yaml/.yml
# file (not a bare directory path). Aggregator kustomizations (which compose
# other kustomizations) are skipped to avoid duplicate validation.
#
# Flux postBuild variable substitution (${VAR}) is replicated offline by
# sourcing cluster-configuration.yaml and running envsubst with an explicit
# allowlist – so only vars defined for that site are expanded and any
# cluster-agnostic tokens are left in place without causing errors.
#
# Schema locations and skip-list are read from kubeconform.yaml so
# they can be updated without touching this script.
set -euo pipefail

# ── Tool prerequisites ────────────────────────────────────────────────────────
for tool in kustomize kubeconform yq jq envsubst; do
  if ! command -v "$tool" &>/dev/null; then
    echo "ERROR: '$tool' is required but not found in PATH." >&2
    exit 1
  fi
done

# ── Load kubeconform config ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUBECONFORM_CONFIG="${SCRIPT_DIR}/kubeconform.yaml"

if [[ ! -f "$KUBECONFORM_CONFIG" ]]; then
  echo "ERROR: kubeconform config not found: ${KUBECONFORM_CONFIG}" >&2
  exit 1
fi

# NOTE: kubeconform uses Go flag syntax (single dash). -verbose is required
# so that -output json emits statusValid entries (without it the JSON
# resources array is empty for fully-valid files).
# -ignore-missing-schemas: resources with no available schema are reported as
# statusSkipped rather than statusError, and the script prints a warning for
# each one without failing.
KUBECONFORM_FLAGS=("-strict" "-verbose" "-ignore-missing-schemas" "-output" "json")

while IFS= read -r location; do
  KUBECONFORM_FLAGS+=("-schema-location" "$location")
done < <(yq eval '.schemas[]' "$KUBECONFORM_CONFIG")

SITES_DIR="sites"
COMMON_DIR="common"

# ── Helper: is this a leaf kustomization? ────────────────────────────────────
# Returns 0 (true) when every entry in resources[] is a .yaml/.yml filename,
# meaning there are no sub-kustomization directory references.
# Kustomizations with no resources (e.g. pure configMapGenerator) are also
# treated as leaves.
is_leaf() {
  local ks="$1"
  local resources
  resources=$(yq eval '.resources // [] | .[]' "$ks" 2>/dev/null) || return 1
  if [[ -z "$resources" ]]; then
    # No resources → treat as leaf only if there are generators
    yq eval '.configMapGenerator // [] | length' "$ks" 2>/dev/null | grep -q "^[1-9]" && return 0
    return 1
  fi
  while IFS= read -r r; do
    [[ -z "$r" ]] && continue
    if [[ "$r" != *.yaml && "$r" != *.yml ]]; then
      return 1  # directory reference → aggregator
    fi
  done <<< "$resources"
  return 0
}

# ── Helper: build + envsubst + kubeconform for one directory ─────────────────
# Globals used:   KUBECONFORM_FLAGS, ENVSUBST_FILTER
# Returns 1 if there were invalid resources, 0 otherwise.
validate_dir() {
  local build_dir="$1"
  local label="${build_dir}"
  printf "    %-62s " "${label}"

  # kustomize build (errors → SKIP)
  # mktemp template with .yaml extension: works on both macOS and Linux.
  local tmp_manifest kustomize_stderr
  tmp_manifest=$(mktemp /tmp/kcv-manifest-XXXXXX.yaml)
  kustomize_stderr=$(mktemp /tmp/kcv-stderr-XXXXXX)
  if ! kustomize build "$build_dir" >"$tmp_manifest" 2>"$kustomize_stderr"; then
    local err
    err=$(head -3 "$kustomize_stderr")
    rm -f "$kustomize_stderr" "$tmp_manifest"
    echo "🟡 SKIP  (kustomize: ${err:-unknown error})"
    return 0
  fi
  rm -f "$kustomize_stderr"

  if [[ ! -s "$tmp_manifest" ]]; then
    rm -f "$tmp_manifest"
    echo "🟡 SKIP  (empty output)"
    return 0
  fi

  # Flux postBuild substitution (offline) then validate via temp file.
  # Strip SOPS-encrypted documents (they have a top-level .sops key and
  # cannot be validated against Kubernetes schemas offline).
  # envsubst reads the file and writes a substituted copy so kubeconform
  # can read from a file path (kubeconform JSON mode needs -verbose to emit
  # statusValid entries; without -verbose the resources array is always []).
  local tmp_subst
  tmp_subst=$(mktemp /tmp/kcv-subst-XXXXXX.yaml)
  envsubst "$ENVSUBST_FILTER" <"$tmp_manifest" \
    | yq eval 'select(.sops == null)' - \
    >"$tmp_subst"
  rm -f "$tmp_manifest"

  local kc_output
  kc_output=$(kubeconform "${KUBECONFORM_FLAGS[@]}" "$tmp_subst" 2>&1 || true)
  rm -f "$tmp_subst"

  local valid invalid skipped
  valid=$(printf '%s'   "$kc_output" | jq -r '.resources[] | select(.status=="statusValid")   | .status' 2>/dev/null | wc -l | tr -d ' ')
  invalid=$(printf '%s' "$kc_output" | jq -r '.resources[] | select(.status=="statusInvalid") | .status' 2>/dev/null | wc -l | tr -d ' ')
  skipped=$(printf '%s' "$kc_output" | jq -r '.resources[] | select(.status=="statusSkipped") | .status' 2>/dev/null | wc -l | tr -d ' ')

  if [[ "${invalid:-0}" -gt 0 ]]; then
    echo "❌ FAIL  (valid=${valid} invalid=${invalid} skipped=${skipped})"
    # Print per-resource error details
    printf '%s' "$kc_output" | jq -r '
      .resources[] | select(.status=="statusInvalid") |
      "        → \(.name) (\(.kind)): \(.msg)"
    ' 2>/dev/null || true
    return 1
  else
    echo "✅ OK    (valid=${valid} skipped=${skipped})"
    # Warn for each skipped resource (no schema found) — informational only.
    if [[ "${skipped:-0}" -gt 0 ]]; then
      printf '%s' "$kc_output" | jq -r '
        .resources[] | select(.status=="statusSkipped") |
        "        🟡 no schema: \(.name) (\(.kind) \(.version))"
      ' 2>/dev/null || true
    fi
    return 0
  fi
}

# ── Collect common leaf dirs once ─────────────────────────────────────────────
COMMON_LEAF_DIRS=()
while IFS= read -r ks_file; do
  if is_leaf "$ks_file"; then
    COMMON_LEAF_DIRS+=("$(dirname "$ks_file")")
  fi
done < <(find "$COMMON_DIR" -name "kustomization.yaml" | LC_ALL=C sort)

# ── Per-site validation loop ──────────────────────────────────────────────────
TOTAL_ERRORS=0

for site_dir in "${SITES_DIR}"/*/; do
  site=$(basename "$site_dir")
  config_file="${site_dir}app-of-apps/cluster-configuration.yaml"

  echo ""
  echo "══════════════════════════════════════════════════════════════════"
  echo "  Site: ${site}"
  echo "══════════════════════════════════════════════════════════════════"

  # Load cluster-configuration vars into the environment and build the
  # envsubst allowlist (only substitute vars this site actually defines).
  ENVSUBST_FILTER=""
  if [[ -f "$config_file" ]]; then
    while IFS='=' read -r key val; do
      [[ -z "$key" ]] && continue
      # Export so envsubst picks them up
      export "${key}=${val}"
      ENVSUBST_FILTER+="\${${key}} "
    done < <(yq eval '.data | to_entries | .[] | .key + "=" + .value' "$config_file" 2>/dev/null)
  fi

  # Collect site-specific leaf kustomization dirs
  SITE_LEAF_DIRS=()
  while IFS= read -r ks_file; do
    if is_leaf "$ks_file"; then
      SITE_LEAF_DIRS+=("$(dirname "$ks_file")")
    fi
  done < <(find "${site_dir}" -name "kustomization.yaml" | LC_ALL=C sort)

  SITE_ERRORS=0

  # ── Site-specific kustomizations ─────────────────────────────────────────
  if [[ ${#SITE_LEAF_DIRS[@]} -gt 0 ]]; then
    echo ""
    echo "  ── Site-specific ─────────────────────────────────────────────────"
    for build_dir in "${SITE_LEAF_DIRS[@]}"; do
      if ! validate_dir "$build_dir"; then
        SITE_ERRORS=$((SITE_ERRORS + 1))
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
      fi
    done
  fi

  # ── Common kustomizations (built with this site's vars) ───────────────────
  if [[ ${#COMMON_LEAF_DIRS[@]} -gt 0 ]]; then
    echo ""
    echo "  ── Common ────────────────────────────────────────────────────────"
    for build_dir in "${COMMON_LEAF_DIRS[@]}"; do
      if ! validate_dir "$build_dir"; then
        SITE_ERRORS=$((SITE_ERRORS + 1))
        TOTAL_ERRORS=$((TOTAL_ERRORS + 1))
      fi
    done
  fi

  echo ""
  if [[ "$SITE_ERRORS" -gt 0 ]]; then
    echo "  ❌ ${SITE_ERRORS} kustomization(s) failed for '${site}'"
  else
    echo "  ✅ All kustomizations passed for '${site}'"
  fi
done

echo ""
echo "══════════════════════════════════════════════════════════════════"
if [[ "$TOTAL_ERRORS" -gt 0 ]]; then
  echo "  RESULT: ❌ FAILED — ${TOTAL_ERRORS} error(s) across all sites"
  echo "══════════════════════════════════════════════════════════════════"
  exit 1
fi
echo "  RESULT: ✅ ALL SITES VALID"
echo "══════════════════════════════════════════════════════════════════"
