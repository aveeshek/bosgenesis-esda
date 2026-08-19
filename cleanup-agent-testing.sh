#!/usr/bin/env bash

set -Eeuo pipefail

NAMESPACE="agent-testing"
KUBECONFIG_PATH="${KUBECONFIG:-}"
ASSUME_YES=false
DRY_RUN=false

usage() {
  cat <<'EOF'
Clean the agent-testing namespace after an ESDA demo mutation.

Usage:
  ./cleanup-agent-testing.sh [--kubeconfig PATH] [--yes] [--dry-run]

Options:
  --kubeconfig PATH  Use an explicit kubeconfig instead of the current context.
  --yes              Skip the interactive confirmation.
  --dry-run          Show the resources that would be removed without deleting them.
  -h, --help         Show this help text.

The script preserves the agent-testing namespace, its default/system objects, and
the long-lived BOS Genesis MCP Roles and RoleBindings needed for the next test.
EOF
}

while (($# > 0)); do
  case "$1" in
    --kubeconfig)
      [[ $# -ge 2 ]] || { echo "ERROR: --kubeconfig requires a path." >&2; exit 2; }
      KUBECONFIG_PATH="$2"
      shift 2
      ;;
    --yes)
      ASSUME_YES=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl is required." >&2; exit 1; }
command -v helm >/dev/null 2>&1 || { echo "ERROR: helm is required." >&2; exit 1; }

KUBECTL=(kubectl)
HELM=(helm)
if [[ -n "$KUBECONFIG_PATH" ]]; then
  [[ -f "$KUBECONFIG_PATH" ]] || { echo "ERROR: kubeconfig not found: $KUBECONFIG_PATH" >&2; exit 1; }
  KUBECTL+=(--kubeconfig "$KUBECONFIG_PATH")
  HELM+=(--kubeconfig "$KUBECONFIG_PATH")
fi

context="$(${KUBECTL[@]} config current-context 2>/dev/null || true)"
echo "Target context: ${context:-unknown}"
echo "Target namespace: $NAMESPACE"
echo

if ! "${KUBECTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "ERROR: Namespace '$NAMESPACE' does not exist." >&2
  exit 1
fi

echo "Current Helm releases:"
"${HELM[@]}" list -n "$NAMESPACE" || true
echo
echo "Current workload and persistent resources:"
"${KUBECTL[@]}" get all,pvc,ingress -n "$NAMESPACE" || true

if $DRY_RUN; then
  echo
  echo "Dry run only. No resources were deleted."
  exit 0
fi

if ! $ASSUME_YES; then
  echo
  read -r -p "Delete all demo workloads and PVC data from '$NAMESPACE'? Type agent-testing to continue: " confirmation
  [[ "$confirmation" == "$NAMESPACE" ]] || { echo "Cleanup cancelled."; exit 0; }
fi

echo
echo "Deleting ClickHouseInstallation resources while the operator is still available..."
"${KUBECTL[@]}" delete clickhouseinstallations.clickhouse.altinity.com \
  --all -n "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=180s || \
  echo "WARNING: ClickHouseInstallation cleanup was unavailable or timed out; residual checks will continue." >&2

mapfile -t releases < <("${HELM[@]}" list -n "$NAMESPACE" -q)
for release in "${releases[@]}"; do
  [[ -n "$release" ]] || continue
  echo "Uninstalling Helm release: $release"
  "${HELM[@]}" uninstall "$release" -n "$NAMESPACE" --wait --timeout 5m
done

echo "Deleting residual namespaced workloads..."
"${KUBECTL[@]}" delete all --all -n "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" delete jobs,cronjobs,ingresses,networkpolicies,poddisruptionbudgets \
  --all -n "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=180s

echo "Deleting persisted demo data..."
"${KUBECTL[@]}" delete pvc --all -n "$NAMESPACE" --ignore-not-found=true --wait=true --timeout=180s

echo "Removing the temporary ClickHouse CRD-reader RBAC used by the demo..."
"${KUBECTL[@]}" delete clusterrolebinding \
  esda-signoz-clickhouse-crd-reader-agent-testing --ignore-not-found=true || true
"${KUBECTL[@]}" delete clusterrole \
  esda-signoz-clickhouse-crd-reader-agent-testing --ignore-not-found=true || true

echo
echo "Verifying cleanup..."
remaining_helm="$(${HELM[@]} list -n "$NAMESPACE" -q)"
remaining_resources="$(${KUBECTL[@]} get all,pvc,ingress -n "$NAMESPACE" -o name 2>/dev/null || true)"
remaining_chi="$(${KUBECTL[@]} get clickhouseinstallations.clickhouse.altinity.com -n "$NAMESPACE" -o name 2>/dev/null || true)"

if [[ -n "$remaining_helm" || -n "$remaining_resources" || -n "$remaining_chi" ]]; then
  echo "ERROR: Cleanup finished with residual resources:" >&2
  [[ -n "$remaining_helm" ]] && printf 'Helm releases:\n%s\n' "$remaining_helm" >&2
  [[ -n "$remaining_resources" ]] && printf 'Namespaced resources:\n%s\n' "$remaining_resources" >&2
  [[ -n "$remaining_chi" ]] && printf 'ClickHouse resources:\n%s\n' "$remaining_chi" >&2
  exit 1
fi

echo "Cleanup complete. '$NAMESPACE' contains no demo workloads, PVCs, Ingresses, ClickHouseInstallation resources, or Helm releases."
echo "The namespace and its BOS Genesis MCP access configuration were preserved."
