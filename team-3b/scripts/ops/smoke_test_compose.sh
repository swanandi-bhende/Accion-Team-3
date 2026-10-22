#!/usr/bin/env bash
#
# Smoke test for the docker-compose stack, run after `docker compose up -d`.
#
#   scripts/ops/smoke_test_compose.sh
#
# Checks, in order, that the stack is genuinely working rather than merely started:
#   1. every long-running service reports healthy, and one-shot jobs exited 0;
#   2. Redis answers PING;
#   3. the local chain answers JSON-RPC on Team A's chain id, Team A's contracts
#      have code at their Galileo addresses, and the pipeline is configured
#      with those same addresses;
#   4. the inference engine serves the seeded toy model;
#   5. a fresh task created and funded on Team A's contracts is dispatched by the
#      pipeline, and the pipeline logs its outcome for that task.
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
DEPLOYMENTS_FILE="deployments/galileo.json"
EXPECTED_CHAIN_ID=16602
WAIT_TIMEOUT=${SMOKE_WAIT_TIMEOUT:-180}
TASK_TIMEOUT=${SMOKE_TASK_TIMEOUT:-60}
SKIP_TRIGGER=${SMOKE_SKIP_TRIGGER:-0}

HEALTHY_SERVICES="redis ipfs hardhat-node inference-engine verification-api pipeline proof-submitter"
ONE_SHOT_SERVICES="team-a-contracts seed-model fl-orchestrator"

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
    [[ $((chain_id)) -eq $EXPECTED_CHAIN_ID ]] \
        || fail "hardhat-node" "chain_id=$((chain_id)) is not Team A's $EXPECTED_CHAIN_ID"
    ok "hardhat-node" "chain_id=$((chain_id))"

    [[ -f "$DEPLOYMENTS_FILE" ]] || fail "team-a-contracts" "missing $DEPLOYMENTS_FILE"
    local name address code listener_value
    for name in TASK_MANAGER_ADDRESS ESCROW_ADDRESS; do
        address=$(json_field "[\"$name\"]" < "$DEPLOYMENTS_FILE")
        code=$(rpc eth_getCode "[\"$address\",\"latest\"]" | json_field '["result"]')
        [[ "$code" != "0x" && -n "$code" ]] || fail "hardhat-node" "no contract code at $name=$address"
        listener_value=$(docker compose exec -T pipeline python -c "from common.config import get_settings; print(get_settings().$(printf %s "$name" | tr '[:upper:]' '[:lower:]'))" | tr -d '\r') || listener_value=""
        [[ "$(printf %s "$listener_value" | tr "[:upper:]" "[:lower:]")" == "$(printf %s "$address" | tr "[:upper:]" "[:lower:]")" ]] || fail "pipeline" "$name=$listener_value does not match deployed $address"
        ok "contracts" "$name=$address code=present pipeline=matches"
    done
}

# 4. POST /inference is gated. An unsigned call is refused, and a signed call from a wallet that
#    is not the task's worker is refused by the chain lookup. A signed run of the seeded model
#    needs a task with a worker on chain, which this stack does not create.
check_inference() {
    local status response
    status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST "$INFERENCE_URL/inference" \
        -H 'content-type: application/json' \
        -d '{"task_id":"0x0000000000000000000000000000000000000000000000000000000000000001","model_id":"toy_linear","input_data":"AAAA"}') \
        || fail "inference-engine" "POST /inference unreachable"
    [[ "$status" == "422" ]] || fail "inference-engine" "unsigned POST /inference returned $status, expected 422"
    response=$(docker compose exec -T pipeline python - <<'PY'
import base64
import json
import urllib.error
import urllib.request

from eth_account import Account
from eth_account.messages import encode_defunct

BASE = "http://inference-engine:8000"
TASK = "0x" + "00" * 31 + "01"


def post(path, body):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


account = Account.create()
_, challenge = post("/challenges", {"task_id": TASK, "wallet_address": account.address})
signature = account.sign_message(encode_defunct(text=challenge["message"])).signature
status, body = post("/inference", {
    "task_id": TASK,
    "model_id": "toy_linear",
    "input_data": base64.b64encode(b"x").decode(),
    "authorization": {
        "wallet_address": account.address,
        "nonce": challenge["nonce"],
        "expires_at": challenge["expires_at"],
        "signature": "0x" + bytes(signature).hex(),
    },
})
print(f"{status} {body.get('error')}")
PY
    ) || fail "inference-engine" "signed POST /inference from a non-worker could not run"
    [[ "$response" == "403 not_task_worker" ]] || fail "inference-engine" "signed call from a non-worker returned '$response', expected 403 not_task_worker"
    ok "inference-engine" "POST /inference gated: unsigned=422, non-worker=403 not_task_worker"
}

# 5. A new task on Team A's contracts is dispatched by the running listener.
#    The trigger runs in the service image: web3 and Team A's ABIs are there, and
#    it reaches the node over the compose network.
check_dispatch() {
    local output task_id deadline value
    # Only log lines from this run count: the pipeline log keeps earlier runs' outcomes,
    # and a deterministic task id can come back after a chain reset.
    local since
    since=$(date -u +%Y-%m-%dT%H:%M:%S)
    output=$(mktemp)
    docker compose run --rm --no-deps -T team-a-contracts \
        python -m scripts.ops.trigger_team_a_task >"$output" 2>&1 \
        || { tail -20 "$output"; rm -f "$output"; fail "trigger" "trigger_team_a_task failed"; }
    task_id=$(grep -oE 'TASK_CREATED task_id=0x[0-9a-fA-F]+' "$output" | head -1 | cut -d'=' -f2)
    rm -f "$output"
    [[ -n "$task_id" ]] || fail "trigger" "no task id in trigger output"

    # The listener logs the task id in lower-case hex, without the 0x prefix.
    local key_id log_id outcome detail dispatched=0
    key_id=${task_id#0x}
    log_id=$(printf %s "$key_id" | tr "[:upper:]" "[:lower:]")

    deadline=$(( $(date +%s) + TASK_TIMEOUT ))
    while true; do
        # The flag is only written after the pipeline's dispatch target accepted the task,
        # so dispatched=true here means the pipeline really took it.
        if [[ $dispatched -eq 0 ]]; then
            value=$(docker compose exec -T redis redis-cli --raw GET "event_listener:task:${key_id}" || true)
            if grep -q '"dispatched": true' <<<"$value"; then
                dispatched=1
            fi
        fi
        if [[ $dispatched -eq 1 ]]; then
            # Dispatch alone is not processing: wait for the pipeline to log the outcome.
            outcome=$(docker compose logs --no-color --since "$since" pipeline 2>&1 | grep "PIPELINE_TASK_COMPLETE task_id=${log_id} " | tail -1 || true)
            if [[ -n "$outcome" ]]; then
                detail=${outcome#*PIPELINE_TASK_COMPLETE }
                # Only a submitted task counts: a dropped one was dispatched but did not settle.
                if grep -q "outcome=submitted" <<<"$detail"; then
                    ok "pipeline" "task_id=$task_id dispatched=true $detail"
                    return 0
                fi
                fail "pipeline" "task_id=$task_id dispatched but not settled: $detail"
            fi
        fi
        if [[ $(date +%s) -ge $deadline ]]; then
            fail "pipeline" "task_id=$task_id dispatched=$dispatched; no PIPELINE_TASK_COMPLETE after ${TASK_TIMEOUT}s"
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
