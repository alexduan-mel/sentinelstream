#!/usr/bin/env bash
# Deploys one image to the EC2 host. Runs on the host, sent by the Deploy workflow via SSM Run Command,
# with the Compose files already written to $APP_DIR/compose.
#
#   deploy.sh <image-uri> <aws-region>      e.g. deploy.sh 1234.dkr.ecr.us-east-1.amazonaws.com/sentinelstream:<sha> us-east-1
#
# Steps: ECR login → secrets from Parameter Store → compose pull/up → /healthz must report the new SHA.
# If the new version fails its health check, the previously deployed image is started again.
set -euo pipefail

IMAGE="${1:?usage: deploy.sh <image-uri> <aws-region>}"
REGION="${2:?usage: deploy.sh <image-uri> <aws-region>}"

PROJECT="sentinelstream"
APP_DIR="/opt/${PROJECT}"
ENV_DIR="/run/${PROJECT}"   # tmpfs: secrets never touch the disk
REGISTRY="${IMAGE%%/*}"
TAG="${IMAGE##*:}"
DEPLOYED_FILE="${APP_DIR}/DEPLOYED"

log() { echo "deploy: $*"; }

compose() {
  (cd "${APP_DIR}/compose" &&
    docker compose --project-name "${PROJECT}" -f docker-compose.yml -f docker-compose.prod.yml "$@")
}

load_secrets() {
  # Every parameter directly under /sentinelstream/ becomes NAME=value in the containers' env file.
  install -d -m 0700 "${ENV_DIR}"
  local tmp="${ENV_DIR}/app.env.tmp"
  (
    umask 077
    aws ssm get-parameters-by-path --region "${REGION}" --path "/${PROJECT}/" --recursive --with-decryption \
      --query 'Parameters[].[Name,Value]' --output text |
      awk -F'\t' -v prefix="/${PROJECT}/" '
        {
          name = substr($1, length(prefix) + 1)
          if (name ~ /^[A-Za-z_][A-Za-z0-9_]*$/) print name "=" substr($0, length($1) + 2)
        }' >"${tmp}"
  )
  mv "${tmp}" "${ENV_DIR}/app.env"
  log "loaded $(wc -l <"${ENV_DIR}/app.env") parameter(s) from /${PROJECT}/"
}

start() {
  local image="$1"
  export SENTINELSTREAM_IMAGE="${image}" AWS_REGION="${REGION}"
  compose pull --quiet
  compose up --detach --no-build --remove-orphans --wait --wait-timeout 120
}

healthy_version() {
  curl -fsS --max-time 5 http://127.0.0.1:8000/healthz |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["version"])'
}

log "deploying ${TAG}"
aws ecr get-login-password --region "${REGION}" |
  docker login --username AWS --password-stdin "${REGISTRY}" >/dev/null
load_secrets

previous=""
[[ -f "${DEPLOYED_FILE}" ]] && previous="$(cat "${DEPLOYED_FILE}")"

if start "${IMAGE}" && [[ "$(healthy_version || true)" == "${TAG}" ]]; then
  echo "${TAG}" >"${DEPLOYED_FILE}"
  docker image prune --all --force --filter "until=168h" >/dev/null
  log "deployed ${TAG}"
  exit 0
fi

log "new version ${TAG} failed its health check"
compose ps || true
compose logs --tail 50 || true
if [[ -n "${previous}" && "${previous}" != "${TAG}" ]]; then
  log "rolling back to ${previous}"
  start "${REGISTRY}/${PROJECT}:${previous}" || log "rollback to ${previous} also failed"
fi
exit 1
