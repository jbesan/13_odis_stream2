#!/usr/bin/env bash
# ==============================================================================
# OD&IS Stream 2 - Setup Cloud Monitoring & Alerting for Cloud Run (odis-app)
# ==============================================================================
# Usage:
#   ./scripts/setup_monitoring.sh [--dry-run]
#
# Environment variables:
#   PROJECT_ID          (default: odis-stream2-app)
#   SERVICE_NAME        (default: odis-app)
#   REGION              (default: europe-west1)
#   PROD_HOST           (default: odis-app-297204448527.europe-west1.run.app)
#   CHAT_CHANNEL_ID     (optional: resource name or ID of Google Chat channel)
#   EMAIL_CHANNEL_ID    (optional: resource name or ID of Email channel)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
POLICIES_DIR="${ROOT_DIR}/infra/monitoring"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "🔍 [DRY-RUN MODE] No changes will be applied."
fi

PROJECT_ID="${PROJECT_ID:-${ODIS_GCP_PROJECT_ID:-odis-stream2-app}}"
SERVICE_NAME="${SERVICE_NAME:-odis-app}"
REGION="${REGION:-${ODIS_GCP_REGION:-europe-west1}}"
PROD_HOST="${PROD_HOST:-odis-app-297204448527.europe-west1.run.app}"

echo "=============================================================================="
echo "OD&IS Monitoring Setup: Project '${PROJECT_ID}' | Service '${SERVICE_NAME}'"
echo "=============================================================================="

# 1. Discover or validate notification channels
echo ""
echo "📡 [1/3] Résolution des canaux de notification..."

RESOLVED_CHAT_CHANNEL="${CHAT_CHANNEL_ID:-}"
RESOLVED_EMAIL_CHANNEL="${EMAIL_CHANNEL_ID:-}"

if [[ -z "${RESOLVED_CHAT_CHANNEL}" || -z "${RESOLVED_EMAIL_CHANNEL}" ]]; then
  echo "Interrogation des canaux existants dans ${PROJECT_ID}..."
  CHANNELS_LIST=$(gcloud beta monitoring channels list --project="${PROJECT_ID}" --format="value(name,type,displayName)" 2>/dev/null || true)

  if [[ -n "${CHANNELS_LIST}" ]]; then
    while IFS=$'\t' read -r c_name c_type c_disp; do
      if [[ -z "${RESOLVED_CHAT_CHANNEL}" && ("${c_type}" == "google_chat" || "${c_disp}" =~ [Cc]hat || "${c_type}" == "webhook_tokenauth") ]]; then
        RESOLVED_CHAT_CHANNEL="${c_name}"
        echo "  -> Canal Chat détecté : ${c_disp} (${c_name})"
      fi
      if [[ -z "${RESOLVED_EMAIL_CHANNEL}" && "${c_type}" == "email" ]]; then
        RESOLVED_EMAIL_CHANNEL="${c_name}"
        echo "  -> Canal Email détecté : ${c_disp} (${c_name})"
      fi
    done <<< "${CHANNELS_LIST}"
  fi
fi

# Fallback sur les canaux créés dans odis-stream2-app
RESOLVED_CHAT_CHANNEL="${RESOLVED_CHAT_CHANNEL:-projects/odis-stream2-app/notificationChannels/9314180061639023081}"
RESOLVED_EMAIL_CHANNEL="${RESOLVED_EMAIL_CHANNEL:-projects/odis-stream2-app/notificationChannels/5337051348539887061}"

if [[ -z "${RESOLVED_CHAT_CHANNEL}" ]]; then
  echo "⚠️ Aucun canal Google Chat trouvé automatiquement."
  echo "  Pour le configurer : Console GCP > Monitoring > Alerting > Edit Notification Channels > Google Chat / Webhook."
  echo "  Ou passez la variable CHAT_CHANNEL_ID=projects/${PROJECT_ID}/notificationChannels/<ID>."
else
  echo "  Canal Chat actif : ${RESOLVED_CHAT_CHANNEL}"
fi

if [[ -z "${RESOLVED_EMAIL_CHANNEL}" ]]; then
  echo "⚠️ Aucun canal Email trouvé automatiquement."
  echo "  Pour le configurer : Console GCP > Monitoring > Alerting > Edit Notification Channels > Email."
  echo "  Ou passez la variable EMAIL_CHANNEL_ID=projects/${PROJECT_ID}/notificationChannels/<ID>."
else
  echo "  Canal Email actif : ${RESOLVED_EMAIL_CHANNEL}"
fi

ALL_CHANNELS=""
if [[ -n "${RESOLVED_CHAT_CHANNEL}" && -n "${RESOLVED_EMAIL_CHANNEL}" ]]; then
  ALL_CHANNELS="${RESOLVED_CHAT_CHANNEL},${RESOLVED_EMAIL_CHANNEL}"
elif [[ -n "${RESOLVED_CHAT_CHANNEL}" ]]; then
  ALL_CHANNELS="${RESOLVED_CHAT_CHANNEL}"
elif [[ -n "${RESOLVED_EMAIL_CHANNEL}" ]]; then
  ALL_CHANNELS="${RESOLVED_EMAIL_CHANNEL}"
fi

# 2. Uptime Check Configuration
echo ""
echo "🩺 [2/3] Configuration de la sonde Uptime Check (${PROD_HOST})..."
UPTIME_CHECK_NAME="odis-app-uptime"

UPTIME_EXISTS=$(gcloud monitoring uptime list-configs --project="${PROJECT_ID}" --filter="displayName='${UPTIME_CHECK_NAME}'" --format="value(name)" 2>/dev/null || true)

if [[ -n "${UPTIME_EXISTS}" ]]; then
  echo "  -> Sonde '${UPTIME_CHECK_NAME}' déjà existante (${UPTIME_EXISTS})."
else
  echo "  -> Création de la sonde Uptime Check sur https://${PROD_HOST}/_stcore/health..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "  [DRY-RUN] gcloud monitoring uptime create ${UPTIME_CHECK_NAME} --project=${PROJECT_ID} --resource-type=uptime-url --resource-labels=host=${PROD_HOST} --protocol=https --path=/_stcore/health --period=5 --timeout=10 --status-classes=2xx --validate-ssl=true"
  else
    gcloud monitoring uptime create "${UPTIME_CHECK_NAME}" \
      --project="${PROJECT_ID}" \
      --resource-type=uptime-url \
      --resource-labels="host=${PROD_HOST}" \
      --protocol=https \
      --path="/_stcore/health" \
      --period=5 \
      --timeout=10 \
      --status-classes=2xx \
      --validate-ssl=true
    echo "  -> Sonde Uptime Check créée avec succès."
  fi
fi

# 3. Alerting Policies Deployment
echo ""
echo "🚨 [3/3] Déploiement des règles d'alerting Cloud Monitoring..."

deploy_policy() {
  local policy_file="$1"
  local policy_channels="$2"
  local display_name
  display_name=$(grep -o '"displayName": "[^"]*"' "${policy_file}" | head -1 | cut -d'"' -f4)

  echo "  Traitement de la règle : '${display_name}'..."

  # Check if policy already exists
  local existing_policy
  existing_policy=$(gcloud monitoring policies list --project="${PROJECT_ID}" --filter="displayName='${display_name}'" --format="value(name)" 2>/dev/null || true)

  if [[ -n "${existing_policy}" ]]; then
    echo "    -> Règle existante trouvée (${existing_policy}). Mise à jour..."
    local update_flags=()
    if [[ -n "${policy_channels}" ]]; then
      update_flags=("--set-notification-channels=${policy_channels}")
    fi
    if [[ "${DRY_RUN}" == "true" ]]; then
      echo "    [DRY-RUN] gcloud monitoring policies update ${existing_policy} --project=${PROJECT_ID} --policy-from-file=${policy_file} ${update_flags[*]:-}"
    else
      gcloud monitoring policies update "${existing_policy}" \
        --project="${PROJECT_ID}" \
        --policy-from-file="${policy_file}" \
        ${update_flags[@]+"${update_flags[@]}"}
      echo "    -> Mise à jour réussie."
    fi
  else
    echo "    -> Création de la règle..."
    local create_flags=()
    if [[ -n "${policy_channels}" ]]; then
      create_flags=("--notification-channels=${policy_channels}")
    fi
    if [[ "${DRY_RUN}" == "true" ]]; then
      echo "    [DRY-RUN] gcloud monitoring policies create --project=${PROJECT_ID} --policy-from-file=${policy_file} ${create_flags[*]:-}"
    else
      gcloud monitoring policies create \
        --project="${PROJECT_ID}" \
        --policy-from-file="${policy_file}" \
        ${create_flags[@]+"${create_flags[@]}"}
      echo "    -> Création réussie."
    fi
  fi
}

# HTTP 5xx -> Chat (ou tous les canaux disponibles)
deploy_policy "${POLICIES_DIR}/http_5xx_alert.json" "${RESOLVED_CHAT_CHANNEL:-${ALL_CHANNELS}}"

# Fuite RAM -> Chat
deploy_policy "${POLICIES_DIR}/memory_utilization_alert.json" "${RESOLVED_CHAT_CHANNEL:-${ALL_CHANNELS}}"

# Uptime Check -> Chat + Email
deploy_policy "${POLICIES_DIR}/uptime_check_alert.json" "${ALL_CHANNELS}"

echo ""
echo "=============================================================================="
echo "✅ Setup Monitoring terminé avec succès pour ${PROJECT_ID} !"
echo "=============================================================================="
