#!/usr/bin/env bash
#
# Smoke test for the docker-compose stack, run after `docker compose up -d`.
#
#   scripts/ops/smoke_test_compose.sh
#
# Checks, in order, that the stack is genuinely working rather than merely started:
#   1. every long-running service reports healthy, and one-shot jobs exited 0;
#   2. Redis answers PING;
#   3. the mock chain answers JSON-RPC and has code at the deployed addresses,
#      and the event-listener is configured with those same addresses;
#   4. the inference engine serves the seeded toy model;
#   5. a fresh task created on the chain is dispatched by the event-listener.
#
# Environment overrides:
#   SMOKE_WAIT_TIMEOUT   seconds to wait for services to become healthy (default 180)
#   SMOKE_TASK_TIMEOUT   seconds to wait for the listener to dispatch (default 60)
#   SMOKE_SKIP_TRIGGER   set to 1 to skip step 5

set -o errexit
set -o pipefail
set -o nounset

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

RPC_URL="http://127.0.0.1:8545"
INFERENCE_URL="http://127.0.0.1:8000"
VERIFICATION_URL="http://127.0.0.1:8001"
DEPLOYMENTS_FILE="mocks/contracts/deployments/localhost.json"
WAIT_TIMEOUT=${SMOKE_WAIT_TIMEOUT:-180}
TASK_TIMEOUT=${SMOKE_TASK_TIMEOUT:-60}
SKIP_TRIGGER=${SMOKE_SKIP_TRIGGER:-0}

HEALTHY_SERVICES="redis ipfs hardhat-node inference-engine verification-api event-listener proof-submitter"
ONE_SHOT_SERVICES="hardhat-deploy seed-model fl-orchestrator"

ok() {
    printf 'SERVICE=%s STATUS=succeeded %s\n' "$1" "$2"
}

fail() {
    printf 'SERVICE=%s STATUS=failed REASON=%s\n' "$1" "$2"
    exit 1
}

rpc() {
    curl -fsS --max-time 10 -X POST "$RPC_URL" \
        -H 'content-type: application/json' \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"$1\",\"params\":$2,\"id\":1}"
}

json_field() {
    python3 -c "import json,sys; print(json.load(sys.stdin)$1)"
}

# 1. Every service is healthy (or, for one-shot jobs, exited 0). Compose reports
#    health asynchronously, so poll until the deadline rather than sampling once.
wait_for_services() {
    local deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
    local report
    while true; do
        report=$(docker compose ps -a --format json 2>/dev/null || true)
        if status_report "$report"; then
            return 0
        fi
        if [[ $(date +%s) -ge $deadline ]]; then
            status_report "$report" --print || true
            fail "stack" "services not ready after ${WAIT_TIMEOUT}s"
        fi
        sleep 3
    done
}

status_report() {
    python3 - "$1" "$HEALTHY_SERVICES" "$ONE_SHOT_SERVICES" "${2:-}" <<'PY'
import json, sys

raw, healthy_names, one_shot_names, mode = sys.argv[1], sys.argv[2].split(), sys.argv[3].split(), sys.argv[4]
try:
    records = json.loads(raw) if raw.strip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
except json.JSONDecodeError:
    records = []
by_service = {record.get("Service"): record for record in records}

ready = True
lines = []
for name in healthy_names:
    record = by_service.get(name)
    health = (record or {}).get("Health") or "missing"
    lines.append(f"  {name}: state={(record or {}).get('State', 'missing')} health={health}")
    if health != "healthy":
        ready = False
for name in one_shot_names:
    record = by_service.get(name)
    state = (record or {}).get("State")
    code = (record or {}).get("ExitCode")
    lines.append(f"  {name}: state={state} exit={code}")
    if state != "exited" or code != 0:
        ready = False

if mode == "--print":
    print("\n".join(lines), file=sys.stderr)
sys.exit(0 if ready else 1)
PY
}

# 2. Redis
check_redis() {
    local reply
    reply=$(docker compose exec -T redis redis-cli ping | tr -d '\r')
    [[ "$reply" == "PONG" ]] || fail "redis" "ping returned '$reply'"
    ok "redis" "ping=PONG"
}

# 3. Chain and deployed contracts
check_chain() {
    local chain_id
    chain_id=$(rpc eth_chainId '[]' | json_field '["result"]') || fail "hardhat-node" "eth_chainId failed"
    [[ -n "$chain_id" ]] || fail "hardhat-node" "empty chain id"
    ok "hardhat-node" "chain_id=$chain_id"

    [[ -f "$DEPLOYMENTS_FILE" ]] || fail "hardhat-deploy" "missing $DEPLOYMENTS_FILE"
    local name address code listener_value
    for name in TASK_MANAGER_ADDRESS ESCROW_ADDRESS; do
        address=$(json_field "[\"$name\"]" < "$DEPLOYMENTS_FILE")
        code=$(rpc eth_getCode "[\"$address\",\"latest\"]" | json_field '["result"]')
        [[ "$code" != "0x" && -n "$code" ]] || fail "hardhat-node" "no contract code at $name=$address"
        listener_value=$(docker compose exec -T event-listener python -c "from common.config import get_settings; print(get_settings().$(printf %s "$name" | tr '[:upper:]' '[:lower:]'))" | tr -d '\r') || listener_value=""
        [[ "$(printf %s "$listener_value" | tr "[:upper:]" "[:lower:]")" == "$(printf %s "$address" | tr "[:upper:]" "[:lower:]")" ]] || fail "event-listener" "$name=$listener_value does not match deployed $address"
        ok "contracts" "$name=$address code=present listener=matches"
    done
}

# 4. The seeded model serves inference through the containerised engine
check_inference() {
    local response
    response=$(curl -fsS --max-time 30 -X POST "$INFERENCE_URL/inference" \
        -H 'content-type: application/json' \
        -d '{"model_id":"toy_linear","input_data":"aGVsbG8="}') \
        || fail "inference-engine" "POST /inference for seeded model toy_linear failed"
    printf '%s' "$response" | grep -q '"output_hash"' || fail "inference-engine" "receipt has no output_hash"
    ok "inference-engine" "model=toy_linear inference=receipt"
}

# 5. A new task on the chain is dispatched by the running listener
check_dispatch() {
    local output task_id deadline value
    output=$(mktemp)
    (cd mocks/contracts && TRIGGER_ONLY=true WEB3_RPC_URL="$RPC_URL" \
        npx hardhat run scripts/deploy_and_trigger.js --network localhost) >"$output" 2>&1 \
        || { tail -20 "$output"; rm -f "$output"; fail "trigger" "deploy_and_trigger.js failed"; }
    task_id=$(grep -oE 'taskId: 0x[0-9a-fA-F]+' "$output" | head -1 | cut -d' ' -f2)
    rm -f "$output"
    [[ -n "$task_id" ]] || fail "trigger" "no taskId in trigger output"

    deadline=$(( $(date +%s) + TASK_TIMEOUT ))
    while true; do
        value=$(docker compose exec -T redis redis-cli --raw GET "event_listener:task:${task_id#0x}" || true)
        if grep -q '"dispatched": true' <<<"$value"; then
            ok "event-listener" "task_id=$task_id dispatched=true"
            return 0
        fi
        if [[ $(date +%s) -ge $deadline ]]; then
            fail "event-listener" "task_id=$task_id not dispatched after ${TASK_TIMEOUT}s"
        fi
        sleep 2
    done
}

main() {
    echo "== smoke_test_compose: start =="
    wait_for_services
    ok "stack" "all services healthy or completed"

    check_redis
    check_chain

    ok "verification-api" "$(curl -fsS --max-time 10 "$VERIFICATION_URL/health")"
    ok "inference-engine" "$(curl -fsS --max-time 10 "$INFERENCE_URL/health")"
    check_inference

    if [[ "$SKIP_TRIGGER" == "1" ]]; then
        ok "trigger" "skipped (SMOKE_SKIP_TRIGGER=1)"
    else
        check_dispatch
    fi
    echo "== smoke_test_compose: end =="
}

main "$@"
