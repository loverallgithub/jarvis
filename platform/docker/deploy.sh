#!/usr/bin/env bash
# ===========================================================================
# JPD deploy — build, verify, roll, prove.
#
# usage: ./deploy.sh <core_version>
#
# The commerce service is pinned SEPARATELY, in ./COMMERCE_VERSION. Rolling it
# is a deliberate edit to that file, and doing so runs the journey tests first
# — they are blocking. That is the practical form of C5: the money path is not
# redeployed for feature work.
#
# Order matters; each step exists because skipping it burned Pimlico:
#   1. import-check inside the built image  (py_compile is NOT an import check)
#   2. journey tests before any commerce roll
#   3. deploy with an EXPLICIT VERSION TAG  (:latest is a silent no-op)
#   4. verify EVERY service by IMAGE ID    (not by "converged", not by 1/1)
#   5. re-probe exposure from OUTSIDE      (host-local curl proves nothing)
#
# Rule 4 says EVERY service because it once did not. Console was checked only
# by replica count, which the OLD task satisfies under `order: start-first` —
# and console is where `jpd` runs.
# ===========================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STACK="${JPD_STACK:-jarvis}"
VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
  echo "usage: $0 <core_version>   e.g. $0 v4" >&2
  echo "  Deploy with an EXPLICIT version tag. Never ':latest' — when the" >&2
  echo "  service spec already says ':latest', updating to ':latest' rolls" >&2
  echo "  NOTHING and reports success." >&2
  exit 1
fi

[[ -f "$HERE/.env" ]] || { echo "missing $HERE/.env" >&2; exit 1; }
set -a; . "$HERE/.env"; set +a
: "${JARVIS_REDIS_PASSWORD:?JARVIS_REDIS_PASSWORD must be set in .env}"

COMMERCE_VERSION="$(cat "$HERE/COMMERCE_VERSION" 2>/dev/null || echo "$VERSION")"
NET="${STACK}_jarvis_net"

echo "==> core=${VERSION}  commerce=${COMMERCE_VERSION}"

# --- preflight: the console must never publish a port -----------------------
# 🔴 /ui has NO AUTH. On 2026-08-08 `ports: ["127.0.0.1:8905:8905"]` was added
# here believing it bound loopback; swarm ingress DISCARDS the host-IP prefix,
# so it became `LISTEN *:8905` — publicly reachable, with no firewall DROP
# because 8905 is absent from SWARM_PORTS in /etc/pimlico-firewall.sh.
#
# This gate lives in deploy.sh rather than the test suite on purpose: the stack
# file is outside the image build context, so a pytest check can only ever SKIP
# in the deployed-image run that is this project's standard. A skipped security
# test reads green while checking nothing. This cannot skip.
echo "==> preflight: console must publish no ports"
# Comments are stripped FIRST. The stack file documents the forbidden mapping in
# prose ("`127.0.0.1:8905:8905` DOES NOT WORK HERE"); scanning raw text makes the
# gate fire on its own warning — which it did, blocking a correct deploy — and
# teaches the next person to delete the documentation to get unblocked.
STACK_NOCOMMENT="$(sed 's/#.*$//' "$HERE/docker-stack.swarm.yml")"
if printf '%s\n' "$STACK_NOCOMMENT" \
   | awk '/^  console:/{c=1;next} /^  [a-z0-9_-]+:/{c=0} /^[a-z0-9_-]+:/{c=0} c' \
   | grep -qE '^[[:space:]]*ports:[[:space:]]*$'; then
  echo "    REFUSING TO DEPLOY: the console service declares a ports: block." >&2
  echo "    /ui is unauthenticated and swarm ingress ignores a 127.0.0.1" >&2
  echo "    prefix, so ANY published port here is public on every interface." >&2
  echo "    Use 'jpd ui --out FILE' or \"ssh <host> 'jpd ui'\" instead." >&2
  exit 1
fi
if printf '%s\n' "$STACK_NOCOMMENT" | grep -nE '[0-9.]*:?8905:8905'; then
  echo "    REFUSING TO DEPLOY: an 8905 port mapping is present (above)." >&2
  exit 1
fi
echo "    OK: console publishes nothing"

echo "==> building jarvis/core:${VERSION}"
docker build -t "jarvis/core:${VERSION}" -t jarvis/core:latest "$ROOT/services/core"

echo "==> import check inside the built image"
docker run --rm --entrypoint python "jarvis/core:${VERSION}" -c "
import jarvis, jarvis.cli, jarvis.main, jarvis.commerce_app, jarvis.console_app
import jarvis.db, jarvis.config
import jarvis.runtime.engine, jarvis.runtime.registry, jarvis.runtime.checkpoints
import jarvis.runtime.lease, jarvis.runtime.watermark, jarvis.runtime.types
import jarvis.connectors.base
import jarvis.commerce.orders, jarvis.commerce.fulfilment, jarvis.commerce.delivery
import jarvis.commerce.offers, jarvis.commerce.pricing, jarvis.commerce.notify
import jarvis.commerce.providers.ghl, jarvis.commerce.providers.stub
import jarvis.console.telegram, jarvis.console.tasks, jarvis.console.human
import jarvis.console.poller, jarvis.console.channels, jarvis.console.cards
import jarvis.console.schemas
print('import check ok')"

echo "==> ensuring swarm secret jarvis_pg_password_v1"
if ! docker secret inspect jarvis_pg_password_v1 >/dev/null 2>&1; then
  : "${JARVIS_PG_PASSWORD:?JARVIS_PG_PASSWORD must be set in .env on first deploy}"
  printf '%s' "$JARVIS_PG_PASSWORD" | docker secret create jarvis_pg_password_v1 - >/dev/null
  echo "    created"
else
  echo "    exists"
fi

# --- journey tests, blocking, whenever commerce would move ----------------
CURRENT_COMMERCE="$(docker service inspect "${STACK}_commerce" \
  --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' 2>/dev/null | sed 's/@.*//' || true)"
if [[ "$CURRENT_COMMERCE" != "jarvis/core:${COMMERCE_VERSION}" ]]; then
  echo "==> commerce would move (${CURRENT_COMMERCE:-none} -> jarvis/core:${COMMERCE_VERSION})"
  echo "    running JOURNEY TESTS — blocking"
  if ! docker run --rm --network "$NET" \
      -e JPD_TEST_PG_DSN="postgresql://jarvis:${JARVIS_PG_PASSWORD}@postgres:5432/jarvis_test" \
      -e JPD_PACKAGE_ROOT=/app \
      --entrypoint python "jarvis/core:${COMMERCE_VERSION}" \
      -m pytest /app/tests/journey /app/tests/unit/test_pricing.py \
                /app/tests/unit/test_provider_contract.py -q -p no:cacheprovider; then
    echo "    FAIL: journey tests are red — refusing to roll the money path" >&2
    exit 1
  fi
  echo "    journey tests GREEN"
else
  echo "==> commerce stays on jarvis/core:${COMMERCE_VERSION} (unchanged)"
fi

echo "==> deploying stack ${STACK}"
# The dashboard header reads JPD_VERSION. Derive it from the version being
# deployed rather than letting it be set by hand — a dashboard that reports a
# version other than the one actually serving is worse than one reporting none,
# because it is believed.
export JPD_VERSION="${VERSION}"
# --detach=true on purpose. core REFUSES to serve an unmigrated schema (which
# is correct), so on a first deploy it crash-loops until the migration lands.
# Waiting for convergence here would deadlock: the migration cannot run through
# a container that cannot start.
sed -e "s|image: jarvis/core:v1|image: jarvis/core:${VERSION}|" \
    -e "s|image: jarvis/core:v4|image: jarvis/core:${COMMERCE_VERSION}|" \
    "$HERE/docker-stack.swarm.yml" \
  | docker stack deploy -c - "$STACK" --detach=true --resolve-image=never

echo "==> waiting for postgres to be healthy"
st=""
for _ in $(seq 1 60); do
  st=$(docker ps --filter "label=com.docker.swarm.service.name=${STACK}_postgres" \
       --format '{{.Status}}' | head -1)
  [[ "$st" == *"(healthy)"* ]] && break
  sleep 2
done
[[ "$st" == *"(healthy)"* ]] || { echo "    FAIL: postgres never became healthy ($st)" >&2; exit 1; }
echo "    OK: $st"

echo "==> migrating schema (one-shot, on ${NET})"
# Migration is a DELIBERATE ACT, not a startup side effect — otherwise N
# replicas race to run DDL during a rolling deploy.
docker run --rm --network "$NET" \
  -e JPD_PG_HOST=postgres -e JPD_PG_PORT=5432 \
  -e JPD_PG_USER=jarvis -e JPD_PG_DB=jarvis \
  -e JPD_PG_PASSWORD="$JARVIS_PG_PASSWORD" \
  --entrypoint jpd "jarvis/core:${VERSION}" db migrate

# --- convergence, asserted on the invariant that matters ------------------
# ⚠️ Do NOT wait on "a task is Running". With `order: start-first` the OLD task
# is still Running throughout the roll, so a task-count check passes instantly
# and the image assertion then fails against a container that was never
# replaced. Poll for the new image ID to be the one running.
converge() {
  local svc="$1" tag="$2" want cid=""
  want=$(docker image inspect "$tag" --format '{{.Id}}')
  for _ in $(seq 1 90); do
    for c in $(docker ps -q --filter "label=com.docker.swarm.service.name=${STACK}_${svc}"); do
      if [[ "$(docker inspect "$c" --format '{{.Image}}')" == "$want" ]]; then
        echo "$c"; return 0
      fi
    done
    sleep 2
  done
  return 1
}

echo "==> waiting for core to run jarvis/core:${VERSION}"
CORE_CID=$(converge core "jarvis/core:${VERSION}") || {
  echo "    FAIL: core never ran the new image" >&2
  docker service ps "${STACK}_core" --no-trunc | head -6 >&2; exit 1; }
echo "    OK: $(docker inspect "$CORE_CID" --format '{{.Image}}' | cut -c8-20)"

# 🔴 Console is checked by IMAGE ID for the same reason core is, and it is the
# one most costly to get wrong: `jpd` prefers the CONSOLE container (C7), so a
# console that silently failed to roll makes every later `jpd` command report
# the OLD code's behaviour — while `docker service ls` shows the new tag and
# every replica count reads 1/1. Deployed v37 with only the replica check in
# place and console happened to be correct; the script would not have known.
echo "==> waiting for console to run jarvis/core:${VERSION}"
CONSOLE_CID=$(converge console "jarvis/core:${VERSION}") || {
  echo "    FAIL: console never ran the new image" >&2
  docker service ps "${STACK}_console" --no-trunc | head -6 >&2; exit 1; }
echo "    OK: $(docker inspect "$CONSOLE_CID" --format '{{.Image}}' | cut -c8-20)"

echo "==> waiting for commerce to run jarvis/core:${COMMERCE_VERSION}"
COMM_CID=$(converge commerce "jarvis/core:${COMMERCE_VERSION}") || {
  echo "    FAIL: commerce never ran its pinned image" >&2
  docker service ps "${STACK}_commerce" --no-trunc | head -6 >&2; exit 1; }
echo "    OK: $(docker inspect "$COMM_CID" --format '{{.Image}}' | cut -c8-20)"

# --- health, with retries -------------------------------------------------
# Winning the image-ID check means the container EXISTS on the new image, not
# that uvicorn has finished binding. Asserting health a second later reports a
# failure that is really a race.
wait_ready() {
  local cid="$1" port="$2"
  for _ in $(seq 1 30); do
    docker exec "$cid" curl -fsS --max-time 3 "http://127.0.0.1:${port}/ready" >/dev/null 2>&1 \
      && return 0
    sleep 2
  done
  return 1
}

echo "==> health"
wait_ready "$CORE_CID" 8900 || { echo "    FAIL: core /ready" >&2
  docker logs --tail 30 "$CORE_CID" >&2; exit 1; }
docker exec "$CORE_CID" curl -fsS http://127.0.0.1:8900/ready && echo
wait_ready "$COMM_CID" 8904 || { echo "    FAIL: commerce /ready" >&2
  docker logs --tail 30 "$COMM_CID" >&2; exit 1; }
docker exec "$COMM_CID" curl -fsS http://127.0.0.1:8904/ready && echo
# Console was previously not readiness-checked at all — only counted. It is the
# service C7 requires to survive a core outage, so it must answer for itself.
wait_ready "$CONSOLE_CID" 8905 || { echo "    FAIL: console /ready" >&2
  docker logs --tail 30 "$CONSOLE_CID" >&2; exit 1; }
docker exec "$CONSOLE_CID" curl -fsS http://127.0.0.1:8905/ready && echo

# --- replica count, asserted separately from /ready -----------------------
# A container can answer /ready while Docker's own HEALTHCHECK fails — e.g. a
# service reusing this image on a different port and inheriting the image's
# probe. The process works, the service is declared unhealthy, and swarm
# restarts it forever. /ready alone will not catch that; the replica count will.
echo "==> replica counts"
for svc in core commerce console; do
  ok=0
  for _ in $(seq 1 45); do
    rep=$(docker service ls --filter "name=${STACK}_${svc}" --format '{{.Replicas}}' | head -1)
    [[ "$rep" == "1/1" ]] && { ok=1; break; }
    sleep 2
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "    FAIL: ${STACK}_${svc} is ${rep:-unknown}, not 1/1" >&2
    cid=$(docker ps -aq --filter "label=com.docker.swarm.service.name=${STACK}_${svc}" | head -1)
    [[ -n "$cid" ]] && docker inspect "$cid" \
      --format '    health={{if .State.Health}}{{.State.Health.Status}}{{range .State.Health.Log}} last={{.Output}}{{end}}{{else}}none{{end}}' >&2
    exit 1
  fi
  echo "    ${STACK}_${svc} 1/1"
done

echo
echo "==> DEPLOYED  core=jarvis/core:${VERSION}  commerce=jarvis/core:${COMMERCE_VERSION}"
echo "    ⚠️  If any PORT changed, re-apply /etc/pimlico-firewall.sh and re-probe"
echo "        from a TEST-NET-3 source WITH a known-open control port. ufw does"
echo "        not cover Docker-published ports and a host-local test proves nothing."
