#!/bin/bash
#
# Scenario 2 Experiment Orchestrator — Inter-VM Communication Latency
#
# Runs 6 conditions (C0–C5, N = 0/1/2/4/6/8 cache+stream workers),
# 10 runs per condition, via SSH into vm-li.  QEMU is rebooted between
# conditions for a clean system state.
#
# Usage:
#   ./experiments/run_scenario2.sh                    # all conditions, 10 runs
#   ./experiments/run_scenario2.sh -n 5               # 5 runs per condition
#   ./experiments/run_scenario2.sh -m 2000            # 2000 pings per run
#   ./experiments/run_scenario2.sh -c C0_baseline     # single condition
#

set -o pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results_scenario2"

RUNS_PER_CONDITION=10
MESSAGES_PER_RUN=1000
SINGLE_CONDITION=""

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=180
SSH_TIMEOUT=120

# ─── Condition Definitions ───────────────────────────────────────────────────
# Each condition is: "N cache_workers N stream_workers" passed to shm_client
# as "-C <N> -S <N>".  C0 (baseline) passes no workers.

CONDITION_NAMES=(
    "C0_baseline"
    "C1_n1"
    "C2_n2"
    "C3_n4"
    "C4_n6"
    "C5_n8"
)

declare -A CONDITION_PARAMS
CONDITION_PARAMS[C0_baseline]=""
CONDITION_PARAMS[C1_n1]="-C 1 -S 1"
CONDITION_PARAMS[C2_n2]="-C 2 -S 2"
CONDITION_PARAMS[C3_n4]="-C 4 -S 4"
CONDITION_PARAMS[C4_n6]="-C 6 -S 6"
CONDITION_PARAMS[C5_n8]="-C 8 -S 8"

# Friendly labels for quick-feedback output
declare -A CONDITION_LABELS
CONDITION_LABELS[C0_baseline]="N=0 (baseline)"
CONDITION_LABELS[C1_n1]="N=1"
CONDITION_LABELS[C2_n2]="N=2"
CONDITION_LABELS[C3_n4]="N=4"
CONDITION_LABELS[C4_n6]="N=6"
CONDITION_LABELS[C5_n8]="N=8"

# ─── Argument Parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--runs)       RUNS_PER_CONDITION="$2"; shift 2 ;;
        -m|--messages)   MESSAGES_PER_RUN="$2";   shift 2 ;;
        -c|--condition)  SINGLE_CONDITION="$2";    shift 2 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Runs per condition (default: 10)"
            echo "  -m, --messages <N>      Pings per run (default: 1000)"
            echo "  -c, --condition <name>  Run only this condition"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Conditions: ${CONDITION_NAMES[*]}"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── Preflight Checks ────────────────────────────────────────────────────────

check_dependencies() {
    local missing=0
    for cmd in sshpass task qemu-system-aarch64; do
        if ! command -v "$cmd" &>/dev/null; then
            echo "ERROR: '$cmd' is not installed."
            missing=1
        fi
    done
    if [[ $missing -eq 1 ]]; then
        echo "Install missing dependencies and retry."
        exit 1
    fi
}

# ─── SSH Helper ──────────────────────────────────────────────────────────────

run_ssh() {
    sshpass -p "$SSH_PASS" ssh $SSH_OPTS -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$@"
}

# ─── QEMU Lifecycle ──────────────────────────────────────────────────────────

QEMU_PID=""

start_qemu() {
    local log_file="$1"
    echo "Starting QEMU..."
    mkdir -p "$RESULTS_DIR"

    (cd "$PROJECT_DIR" && task qemu) > "$log_file" 2>&1 &
    QEMU_PID=$!

    sleep 3
    echo "QEMU started (shell PID: $QEMU_PID)"
}

wait_for_boot() {
    local log_file="$1"
    echo "Waiting for platform to boot (timeout: ${BOOT_TIMEOUT}s)..."
    local elapsed=0
    while [[ $elapsed -lt $BOOT_TIMEOUT ]]; do
        if grep -q "$BOOT_MARKER" "$log_file" 2>/dev/null; then
            echo "Boot marker detected after ${elapsed}s."
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
        if ! pgrep -f qemu-system-aarch64 &>/dev/null; then
            echo "ERROR: QEMU process died unexpectedly."
            echo "Check $log_file for details."
            return 1
        fi
    done
    echo "ERROR: Boot timeout after ${BOOT_TIMEOUT}s."
    return 1
}

wait_for_ssh() {
    echo "Waiting for SSH to become available (timeout: ${SSH_TIMEOUT}s)..."
    local elapsed=0
    while [[ $elapsed -lt $SSH_TIMEOUT ]]; do
        if run_ssh "echo ready" &>/dev/null; then
            echo "SSH is ready."
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo "ERROR: SSH timeout after ${SSH_TIMEOUT}s."
    return 1
}

stop_qemu() {
    echo "Stopping QEMU..."
    if pkill -f qemu-system-aarch64 2>/dev/null; then
        sleep 2
    fi
    if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null
        wait "$QEMU_PID" 2>/dev/null
    fi
    pkill -9 -f qemu-system-aarch64 2>/dev/null
    QEMU_PID=""
    echo "QEMU stopped."
}

trap stop_qemu EXIT

# ─── Run a Single Condition ──────────────────────────────────────────────────

run_condition() {
    local name="$1"
    local params="${CONDITION_PARAMS[$name]}"
    local label="${CONDITION_LABELS[$name]}"
    local cond_dir="$RESULTS_DIR/$name"
    local qemu_log="$cond_dir/qemu_serial.log"
    mkdir -p "$cond_dir"

    local cmd="shm_client -n $MESSAGES_PER_RUN $params"

    echo ""
    echo "============================================"
    echo "Condition: $name  ($label)"
    echo "Command:   $cmd"
    echo "Runs:      $RUNS_PER_CONDITION"
    echo "============================================"

    start_qemu "$qemu_log"
    if ! wait_for_boot "$qemu_log"; then
        echo "ERROR: Failed to boot for condition $name. Skipping."
        stop_qemu
        return 1
    fi
    if ! wait_for_ssh; then
        echo "ERROR: SSH not available for condition $name. Skipping."
        stop_qemu
        return 1
    fi

    # Give shm_echo (PID 1 on vm-hi) a moment to register in proxyshm
    sleep 2

    for run in $(seq 1 "$RUNS_PER_CONDITION"); do
        local run_file="$cond_dir/run_$(printf '%02d' "$run").txt"
        local run_label="[$name] Run $run/$RUNS_PER_CONDITION"

        echo -n "$run_label ... "

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        if [[ $exit_code -ne 0 && $exit_code -ne 1 ]]; then
            # exit 1 means messages were lost — still a valid run, just noted
            echo "FAILED (exit code: $exit_code)"
        else
            # Quick-feedback: extract key metrics from the output
            local lost
            local mean_rtl
            local p99_rtl
            lost=$(grep -oP 'Messages lost:\s+\K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            mean_rtl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            p99_rtl=$(grep -oP 'P99:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            if [[ "$exit_code" -eq 1 ]]; then
                echo "OK (lost: $lost, mean: ${mean_rtl} us, P99: ${p99_rtl} us) [LOSSES DETECTED]"
            else
                echo "OK (lost: $lost, mean: ${mean_rtl} us, P99: ${p99_rtl} us)"
            fi
        fi

        # Brief pause between runs — shm_echo remains running and ready
        sleep 2
    done

    stop_qemu
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    echo "========================================================"
    echo "Scenario 2 Experiment Orchestrator"
    echo "Inter-VM Communication Latency and Reliability"
    echo "========================================================"
    echo "Runs per condition:  $RUNS_PER_CONDITION"
    echo "Pings per run:       $MESSAGES_PER_RUN"
    echo "Results directory:   $RESULTS_DIR"
    echo ""

    check_dependencies

    local conditions_to_run=()
    if [[ -n "$SINGLE_CONDITION" ]]; then
        if [[ -z "${CONDITION_PARAMS[$SINGLE_CONDITION]+x}" ]]; then
            echo "ERROR: Unknown condition '$SINGLE_CONDITION'"
            echo "Available: ${CONDITION_NAMES[*]}"
            exit 1
        fi
        conditions_to_run=("$SINGLE_CONDITION")
    else
        conditions_to_run=("${CONDITION_NAMES[@]}")
    fi

    local total_runs=$(( ${#conditions_to_run[@]} * RUNS_PER_CONDITION ))
    # Each run takes ~(n_messages * RTL + 2s ramp + 2s pause) ≈ 10-30s
    # Plus ~3 min boot overhead per condition
    local est_minutes=$(( (total_runs * 30 + ${#conditions_to_run[@]} * 180) / 60 ))
    echo "Total runs:          $total_runs across ${#conditions_to_run[@]} conditions"
    echo "Estimated time:      ~${est_minutes} minutes"
    echo "QEMU restarts:       once per condition"
    echo ""

    for condition in "${conditions_to_run[@]}"; do
        run_condition "$condition"
    done

    echo ""
    echo "========================================================"
    echo "All conditions completed."
    echo "Results saved to: $RESULTS_DIR"
    echo "========================================================"
    echo ""
    echo "Next: python3 experiments/analyze_scenario2.py"
}

main
