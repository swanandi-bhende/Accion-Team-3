#!/usr/bin/env bash
# -*- mode: shell; indent-tabs-mode: t -*-
#
# Smoke test for the docker-compose stack.
#
# Usage: docker compose up --build -d && scripts/ops/smoke_test_compose.sh
#
# This script validates the full stack by hitting health endpoints, confirming
# the mock chain is reachable, and optionally triggering one real end-to-end task
# by running the Hardhat deploy+trigger script against the live node.

set -o errexit
set -o pipefail
set -o nounset

# Network reach is the expensive part; bail out early rather than print a page
# of failures.
export DEFAULT_DOCKER_TIMEOUT=30
export COMPOSE_HTTP_TIMEOUT=60
export COMPOSE_PROGRESS_TIMEOUT=60

# The chain must be healthy before the tests can meaningfully run. Long enough
# for a first `docker compose up` (npm install + hardhat node) and short enough
# to fail loudly rather than hang.
RPC_WAIT_TIMEOUT=${ROCKSDB_WAIT_TIMEOUT:-40}

# --- helpers ----------------------------------------------------------------
fail() {
    local service="$1"
    local detail="${2:-}"
    printf 'SERVICE=%s STATUS=failed REASON=%s\n' "$service" "$detail"
    return 1
}

check() {
    local service="$1"
    local url="$2"
    local expected_status="${3:-200}"
    local body
    local http_code
    # curl returns the body in stdout; the status goes to stderr via -w.
    body=$(curl -fsS --max-time 10 -w "%{http_code}" "$url" || true)
    http_code="${body: -3}"
    body="${body:0:${#body}-3}"
    if [[ "$http_code" != "$expected_status" ]]; then
        fail "$service" "url=$url expected_status=$expected_status actual_status=$http_code body=$(printf '%s' "$body" | head -c 200)"
        return 1
    fi
    printf 'SERVICE=%s STATUS=succeeded endpoint=%s status=%s\n' "$service" "$url" "$http_code"
    return 0
}

wait_for_rpc() {
    local url="${1:-http://127.0.0.1:8545}"
    local deadline
    deadline=$(( "$(date +%s)" + RPC_WAIT_TIMEOUT ))
    while true; do
        if curl -fsS --max-time 5 -X POST "$url" \
            -H 'content-type: application/json' \
            -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
            >/dev/null 2>&1; then
            printf 'SERVICE=hardhat-node STATUS=succeeded url=%s\n' "$url"
            return 0
        fi
        if [[ "$(date +%s)" -ge "$deadline" ]]; then
            fail "hardhat-node" "rpc not reachable after ${RPC_WAIT_TIMEOUT}s url=$url"
            return 1
        fi
        sleep 1
    done
}

# --- trigger a real task via the Hardhat deploy+trigger script ---------------
trigger_real_task() {
    local deploy_script="mocks/contracts/scripts/deploy_and_trigger.js"
    local hardhat_dir="mocks/contracts"
    local node_modules="$hardhat_dir/node_modules"

    if [[ ! -f "$deploy_script" ]]; then
        fail "trigger" "deploy script missing: $deploy_script"
        return 1
    fi

    # Ensure node_modules exist (hardhat-node volume provides them when the
    # stack is up; otherwise they may not be present in the bare checkout).
    if [[ ! -d "$node_modules" ]]; then
        fail "trigger" "node_modules missing; make sure the stack is up first"
        return 1
    fi

    # Run the deploy+trigger script against the live node. The script deploys
    # contracts (idempotent across restarts because it writes to .env.mock) and
    # then creates+funds a real task. We use the same WEB3_RPC_URL the compose
    # services use so the script reaches the node container, not loopback.
    local exit_code
    cd "$hardhat_dir"
    WEB3_RPC_URL=http://127.0.0.1:8545 \
        npx hardhat run "$deploy_script" --network localhost \
        > /tmp/trigger_output.log 2>&1
    exit_code=$?
    cd - > /dev/null

    if [[ $exit_code -ne 0 ]]; then
        fail "trigger" "deploy_and_trigger.js exited $exit_code; tail -20 /tmp/trigger_output.log"
        return 1
    fi

    # Confirm the script produced the expected output.
    if grep -q "TaskCreated triggered" /tmp/trigger_output.log && \
       grep -q "TaskFunded triggered" /tmp/trigger_output.log; then
        printf 'SERVICE=trigger STATUS=succeeded task_created_and_funded=true\n'
        return 0
    fi

    # If the script ran but didn't trigger (TRIGGER_MOCK_TASK not set), that is
    # fine: the deploy step still ran.
    if grep -q "Wrote addresses to .env.mock" /tmp/trigger_output.log; then
        printf 'SERVICE=trigger STATUS=succeeded deploy_ran=true trigger_skipped=true\n'
        return 0
    fi

    fail "trigger" "unexpected deploy_and_trigger.js output; tail -20 /tmp/trigger_output.log"
    return 1
}

# --- main -------------------------------------------------------------------
main() {
    echo "== smoke_test_compose: start =="

    wait_for_rpc || exit 1

    # 1. Health endpoints
    check "inference-engine" "http://127.0.0.1:8000/health" || exit 1
    check "verification-api" "http://127.0.0.1:8001/health" || exit 1

    # 2. Real RPC check
    local chain_id
    chain_id=$(curl -fsS --max-time 10 -X POST "http://127.0.0.1:8545" \
        -H 'content-type: application/json' \
        -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' | \
        python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["result"])')
    if [[ -z "$chain_id" ]]; then
        fail "hardhat-node" "chainId empty"
        exit 1
    fi
    printf 'SERVICE=hardhat-node STATUS=succeeded chain_id=%s\n' "$chain_id"

    # 3. Trigger a real end-to-end task (reuses the Step 2.8 trigger script).
    trigger_real_task || exit 1

    echo "== smoke_test_compose: end =="
}

main "$@"
