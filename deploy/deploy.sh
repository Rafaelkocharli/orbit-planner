#!/usr/bin/env bash
# Switch the running app to image orbit-planner:<tag>, verify health, roll back on failure.
# Usage: deploy.sh <tag>        (the image must already be loaded: docker load)
#        deploy.sh --rollback   (return to the previous tag)
set -euo pipefail

DIR=/opt/orbit-planner
cd "$DIR"
CURRENT_FILE=.current_tag
PREVIOUS_FILE=.previous_tag

current=$(cat "$CURRENT_FILE" 2>/dev/null || true)
if [[ "${1:-}" == "--rollback" ]]; then
  tag=$(cat "$PREVIOUS_FILE" 2>/dev/null || true)
  [[ -n "$tag" ]] || { echo "No previous tag to roll back to" >&2; exit 1; }
else
  tag=${1:?usage: deploy.sh <tag>|--rollback}
fi
[[ "$tag" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "Invalid tag: $tag" >&2; exit 1; }
docker image inspect "orbit-planner:$tag" >/dev/null

healthy() {
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

switch_to() {
  ORBIT_TAG="$1" docker compose -f compose.yml -p orbit up -d --remove-orphans
}

echo "Deploying orbit-planner:$tag (current: ${current:-none})"
switch_to "$tag"
if healthy; then
  if [[ "$tag" != "$current" && -n "$current" ]]; then echo "$current" > "$PREVIOUS_FILE"; fi
  echo "$tag" > "$CURRENT_FILE"
  # Keep the three newest images for rollbacks.
  docker images orbit-planner --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | tail -n +4 | cut -f2 |
    while read -r old; do [[ "$old" != "$tag" ]] && docker rmi "orbit-planner:$old" >/dev/null 2>&1 || true; done
  echo "OK: orbit-planner:$tag is healthy"
else
  echo "Health check failed for $tag" >&2
  docker compose -f compose.yml -p orbit logs --tail 50 app >&2 || true
  if [[ -n "$current" && "$current" != "$tag" ]]; then
    echo "Rolling back to $current" >&2
    switch_to "$current"
    healthy && echo "Rolled back to $current" >&2
  fi
  exit 1
fi
