#!/usr/bin/env bash
# Load GATEWAY_* from repo .env into the current shell without executing arbitrary lines.
#
# Usage:
#   source scripts/load_gateway_env.sh
#   load_gateway_env /path/to/repo/root

load_gateway_env() {
  local repo_root="${1:-.}"
  local env_file="${repo_root}/.env"
  local key val line

  if [[ -f "${env_file}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
      case "${line}" in
        GATEWAY_*=*)
          key="${line%%=*}"
          val="${line#*=}"
          if [[ "${val}" =~ ^\".*\"$ || "${val}" =~ ^\'.*\'$ ]]; then
            val="${val:1:-1}"
          fi
          if [[ -z "${!key:-}" ]]; then
            printf -v "${key}" '%s' "${val}"
            export "${key?}"
          fi
          ;;
      esac
    done <"${env_file}"
  fi

  export GATEWAY_ADMIN_USER="${GATEWAY_ADMIN_USER:-admin}"
  export GATEWAY_ADMIN_PASSWORD="${GATEWAY_ADMIN_PASSWORD:-change-me-gateway-admin}"
  export GATEWAY_USER="${GATEWAY_USER:-user}"
  export GATEWAY_USER_PASSWORD="${GATEWAY_USER_PASSWORD:-change-me-gateway-user}"
}
