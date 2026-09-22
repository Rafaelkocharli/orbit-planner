#!/usr/bin/env bash
# One-time server preparation (Ubuntu 22.04+). Idempotent, run as root:
#   ssh root@HOST "bash -s -- '$(cat ~/.ssh/orbit_deploy.pub)'" < deploy/server-setup.sh
# Installs Docker and nginx, creates the restricted "deploy" user for CI and the nginx site.
set -euo pipefail

CI_PUBKEY=${1:?pass the CI public key as the first argument}
[[ "$CI_PUBKEY" =~ ^ssh-(ed25519|rsa)\  ]] || { echo "Not an SSH public key" >&2; exit 1; }
DIR=/opt/orbit-planner

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 nginx curl >/dev/null
systemctl enable --now docker nginx >/dev/null 2>&1

# CI user: can use Docker, but its key only runs the entry script below (no shell).
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
mkdir -p "$DIR"
chown deploy:deploy "$DIR"

cat > /usr/local/bin/orbit-ci-entry <<'EOF'
#!/usr/bin/env bash
# Forced command for the CI key. Allowed:
#   load               - docker load an image from stdin (gzip)
#   put compose|deploy - replace compose.yml or deploy.sh from stdin
#   deploy <tag>       - run deploy.sh <tag>;  rollback - deploy.sh --rollback
set -euo pipefail
DIR=/opt/orbit-planner
read -r cmd arg extra <<< "${SSH_ORIGINAL_COMMAND:-}"
[[ -z "${extra:-}" ]] || { echo "Unexpected arguments" >&2; exit 2; }
case "$cmd" in
  load) gunzip | docker load ;;
  put)
    case "$arg" in
      compose) cat > "$DIR/compose.yml.new" && mv "$DIR/compose.yml.new" "$DIR/compose.yml" ;;
      deploy) cat > "$DIR/deploy.sh.new" && chmod 755 "$DIR/deploy.sh.new" && mv "$DIR/deploy.sh.new" "$DIR/deploy.sh" ;;
      *) echo "Unknown file: $arg" >&2; exit 2 ;;
    esac ;;
  deploy) [[ "$arg" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "Invalid tag" >&2; exit 2; }; exec "$DIR/deploy.sh" "$arg" ;;
  rollback) exec "$DIR/deploy.sh" --rollback ;;
  *) echo "Command not allowed: $cmd" >&2; exit 2 ;;
esac
EOF
chmod 755 /usr/local/bin/orbit-ci-entry

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "command=\"/usr/local/bin/orbit-ci-entry\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $CI_PUBKEY" \
  > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

cat > /etc/nginx/sites-available/orbit-planner <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Scenario uploads and exported results can be several megabytes.
    client_max_body_size 50m;
    gzip on;
    gzip_types application/json text/css application/javascript;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/orbit-planner /etc/nginx/sites-enabled/orbit-planner
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

docker --version
docker compose version
echo "Server ready. CI user: deploy, app dir: $DIR"
