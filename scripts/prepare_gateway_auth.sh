#!/usr/bin/env bash
# Generate nginx Basic Auth files from .env (GATEWAY_*).
# Files are gitignored (nginx/.htpasswd-*). Never commit real passwords.
#
#   GATEWAY_ADMIN_USER / GATEWAY_ADMIN_PASSWORD → .htpasswd-admin
#     ( /api/v1/* admin routes, /mlflow, /api/train )
#   GATEWAY_USER / GATEWAY_USER_PASSWORD         → .htpasswd-user
#     ( /api/predict/*, predict-go-* )
#
# Admin and public-user passwords must differ.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=load_gateway_env.sh
source "${REPO_ROOT}/scripts/load_gateway_env.sh"
load_gateway_env "${REPO_ROOT}"

NGINX_DIR="${REPO_ROOT}/nginx"

if ! command -v htpasswd >/dev/null 2>&1; then
  echo "htpasswd not found. Install apache2-utils (Debian/Ubuntu) or httpd-tools (RHEL)." >&2
  exit 1
fi

if [[ -z "${GATEWAY_ADMIN_PASSWORD}" || -z "${GATEWAY_USER_PASSWORD}" ]]; then
  echo "GATEWAY_ADMIN_PASSWORD and GATEWAY_USER_PASSWORD must be set (see .env.example)." >&2
  exit 1
fi

if [[ "${GATEWAY_ADMIN_PASSWORD}" == "${GATEWAY_USER_PASSWORD}" ]]; then
  echo "Refusing to write htpasswd: GATEWAY_ADMIN_PASSWORD must differ from GATEWAY_USER_PASSWORD." >&2
  exit 1
fi

if [[ "${GATEWAY_ADMIN_USER}" == "${GATEWAY_USER}" ]]; then
  echo "Refusing to write htpasswd: GATEWAY_ADMIN_USER must differ from GATEWAY_USER." >&2
  exit 1
fi

mkdir -p "${NGINX_DIR}"
htpasswd -nbB "${GATEWAY_ADMIN_USER}" "${GATEWAY_ADMIN_PASSWORD}" >"${NGINX_DIR}/.htpasswd-admin"
htpasswd -nbB "${GATEWAY_USER}" "${GATEWAY_USER_PASSWORD}" >"${NGINX_DIR}/.htpasswd-user"

echo "Wrote ${NGINX_DIR}/.htpasswd-admin and ${NGINX_DIR}/.htpasswd-user"
echo "Admin (ops):     ${GATEWAY_ADMIN_USER}"
echo "User  (predict): ${GATEWAY_USER}"
echo "Next: recreate streamlit if needed → docker compose up -d streamlit-ui"
echo "UI should use the predict user; admin password is for /mlflow and admin APIs."
