#!/bin/bash
#
# Scenario 2 Experiment Orchestrator — Inter-VM Communication Latency
#
# Runs experiments based on a matrix:
# Stressor types: baseline, cpu, cache, stream, io, worst-case
# Number of workers: 0 (baseline), 4, 8, 16, 32, 64

# 10 runs per condition, via SSH into vm-li.  QEMU is rebooted between
# conditions for a clean system state.
#
# Usage:
#   ./experiments/s2_orchestrator.sh                    # all conditions, 10 runs
#   ./experiments/s2_orchestrator.sh -n 5               # 5 runs per condition
#   ./experiments/s2_orchestrator.sh -m 2000            # 2000 pings per run
#   ./experiments/s2_orchestrator.sh -s baseline        # single condition
#   ./experiments/s2_orchestrator.sh -b                 # run with hicom variant settings
#

set -o pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"

# Results root is chosen after parsing args so -b can switch it
RESULTS_ROOT="$SCRIPT_DIR/results"
RESULTS_HICOM_ROOT="$SCRIPT_DIR/results_hicom"

RUNS_PER_EXPERIMENT=10
PINGS_PER_RUN=2000
SINGLE_STRESSOR=""
HICOM_MODE=0

# Marker strings from shm_ping_hi console output
READY_MARKER="HICOM_PING_READY"
DONE_MARKER_PREFIX="HICOM_PING_DONE run="
READY_TIMEOUT=60   # seconds to wait for HICOM_PING_READY after boot


CLIENT_BIN="shm_client"

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=180
SSH_TIMEOUT=120


STRESSOR_TYPES=(baseline cpu cache stream io "worst-case")
NUM_WORKERS_LIST=(n4 n8)

declare -A NUM_WORKERS_MAP=( [n4]=4 [n8]=8 )

# Condition-level mappings (populated below)
declare -A STRESSOR_WORKERS
declare -A STRESSOR_LABELS

fix_QEMU_serial_log_format() {
    awk '
    function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
    {
        line = $0
        gsub(/\r/, "", line)

        # Strip ANSI colors/sequences
        gsub(/\033\[[0-9;]*[[:alpha:]]/, "", line)

        # Strip QEMU serial prefixes
        sub(/^[[:space:]]*vm-hi[[:space:]]*\|[[:space:]]*/, "", line)
        sub(/^[[:space:]]*vm-hi,\|,/, "", line)

        # Strip kernel log prefixes like "[  552.333704] random: crng init"
        sub(/^\[[[:space:]]*[0-9.]+\][^,]*/, "", line)

        line = trim(line)
        if (line == "") next

        # Remove trailing commas
        sub(/,+$/, "", line)

        if (line != "") print line
    }'
}

build_conditions() {
    local -n out_conditions=$1
    out_conditions=()

    for stressor in "${STRESSOR_TYPES[@]}"; do
        if [[ "$stressor" == "baseline" ]]; then
            local name="baseline"
            STRESSOR_WORKERS["$name"]=0
            out_conditions+=("$name")
            continue
        fi

        for worker_key in "${NUM_WORKERS_LIST[@]}"; do
            local name="${stressor}_${worker_key}"
            STRESSOR_WORKERS["$name"]="${NUM_WORKERS_MAP[$worker_key]}"
            STRESSOR_LABELS["$name"]="${stressor}"
            out_conditions+=("$name")
        done
    done
}

filter_conditions() {
    local -n in_conditions=$1
    local -n out_conditions=$2
    local selected_stressor=$3

    out_conditions=()
    if [[ -z "$selected_stressor" ]]; then
        out_conditions=("${in_conditions[@]}")
        return 0
    fi

    for condition in "${in_conditions[@]}"; do
        if [[ "$condition" == "$selected_stressor"* ]]; then
            out_conditions+=("$condition")
        fi
    done
}

# ─── Argument Parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--runs)       RUNS_PER_EXPERIMENT="$2"; shift 2 ;;
        -p|--pings)   PINGS_PER_RUN="$2";   shift 2 ;;
        -s|--stressor)  SINGLE_STRESSOR="$2";    shift 2 ;;
        -b|--hicom)     HICOM_MODE=1; CLIENT_BIN="shm_trigger"; shift 1 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Runs per experiment (default: 10)"
            echo "  -p, --pings <N>         Pings per run (default: 5000)"
            echo "  -s, --stressor <name>   Run only this stressor"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Stressors: ${STRESSOR_TYPES[*]}"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Choose results dir based on mode
if [[ $HICOM_MODE -eq 1 ]]; then
    RESULTS_DIR="$RESULTS_HICOM_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
else
    RESULTS_DIR="$RESULTS_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
fi


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
        sleep 15
        elapsed=$((elapsed + 15))
        if ! pgrep -f qemu-system-aarch64 &>/dev/null; then
            echo "ERROR: QEMU process died unexpectedly during boot."
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

# Wait for shm_ping_hi to finish initialising and print HICOM_PING_READY.
wait_for_hi_ready() {
    local log_file="$1"
    echo "Waiting for HICOM_PING_READY in hi console (timeout: ${READY_TIMEOUT}s)..."
    local elapsed=0
    while [[ $elapsed -lt $READY_TIMEOUT ]]; do
        if grep -q "$READY_MARKER" "$log_file" 2>/dev/null; then
            echo "shm_ping_hi is ready."
            return 0
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    echo "ERROR: HICOM_PING_READY not seen after ${READY_TIMEOUT}s."
    return 1
}


# Wait for "HICOM_PING_DONE run=<run_idx>" to appear in the console log.
wait_for_hi_done() {
    local log_file="$1"
    local run_idx="$2"
    local marker="${DONE_MARKER_PREFIX}${run_idx}"
    local timeout=300   # a 1000-ping run takes at most ~5 min at slow poll
    local elapsed=0
    while [[ $elapsed -lt $timeout ]]; do
        if grep -q "$marker" "$log_file" 2>/dev/null; then
            return 0
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    echo "ERROR: '${marker}' not seen after ${timeout}s."
    return 1
}

# Extract the result block for run <run_idx> from the console log.
# Saves everything between "HICOM_PING_START run=N" and
# "HICOM_PING_DONE run=N" (inclusive) to <dest_file>.
extract_run_results() {
    local log_file="$1"
    local run_idx="$2"
    local dest_file="$3"

    awk \
        "/HICOM_PING_START run=${run_idx}/{found=1} \
         found{print} \
         /HICOM_PING_DONE run=${run_idx}/{found=0}" \
        "$log_file" | fix_QEMU_serial_log_format > "$dest_file"
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

# ─── Run a given stressor type with a given number of workers ──────────────────────────────────────────────────

run_experiment() {
    
    local name="$1"
    local exp_dir="$RESULTS_DIR/$name"
    local qemu_log="$exp_dir/qemu_serial.log"
    mkdir -p "$exp_dir"

    # name is like "cpu_n4" or "baseline_n0"
    local stressor="${name%%_*}"
    local n_workers=${STRESSOR_WORKERS[$name]:-0}
    local label="${STRESSOR_LABELS[$name]:-N=$n_workers}"

    local cmd="$CLIENT_BIN -n $PINGS_PER_RUN"

    echo "============================================"
    echo "Experiment: $name  ($label)"
    echo "Command:   $cmd" 
    echo "Runs:      $RUNS_PER_EXPERIMENT"
    echo "============================================"

    start_qemu "$qemu_log"
    if ! wait_for_boot "$qemu_log"; then
        echo "ERROR: Failed to boot for experiment $name. Skipping."
        stop_qemu
        return 1
    fi
    if ! wait_for_ssh; then
        echo "ERROR: SSH not available for experiment $name. Skipping."
        stop_qemu
        return 1
    fi

    if [[ $HICOM_MODE -eq 1 ]]; then
        if ! wait_for_hi_ready "$qemu_log"; then
            echo "ERROR: shm_ping_hi not ready for experiment $name. Skipping."
            stop_qemu
            return 1
        fi
    fi

    # Give shm_echo (PID 1 on vm-hi) a moment to register in proxyshm
    sleep 2

    # Start stress-ng once for this condition, before any run begins.
    if [[ $n_workers -gt 0 ]]; then
        echo "Starting stress-ng for stressor '$stressor' with $n_workers workers"
        # Calculate timeout: 10s per run + 5s overhead, capped at 5 minutes
        local stress_timeout=$(( (RUNS_PER_EXPERIMENT * 10) + 5 ))
        [[ $stress_timeout -gt 300 ]] && stress_timeout=300
        
        case "$stressor" in
            cpu)
                run_ssh "stress-ng --cpu $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
            shm)
                # avail_mb=$(run_ssh "awk '/MemAvailable/{print int(\$2/1024)}' /proc/meminfo")
                # Use 70% of available memory, split across workers
                # Cap per-worker at 64M (larger than any L3 cache — enough for cache pressure)
                # Floor per-worker at 4M (below this, negligible cache interference)
                # budget_mb=$(( avail_mb * 70 / 100 ))
                # per_worker_mb=$(( budget_mb / n_workers ))
                # per_worker_mb=$(( per_worker_mb > 64 ? 64 : per_worker_mb ))
                # per_worker_mb=$(( per_worker_mb < 4  ?  4 : per_worker_mb ))
                # echo "Allocating ${per_worker_mb}MB per worker for shm stress (total budget: ${budget_mb}MB)"
                run_ssh "stress-ng --shm $n_workers --shm-bytes 1M --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
            cache)
                run_ssh "stress-ng --cache $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
            stream)
                run_ssh "stress-ng --stream $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
            io)
                run_ssh "stress-ng --io $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
            "worst-case")
                run_ssh "stress-ng --cpu $n_workers --shm $n_workers --shm-bytes 1M --cache $n_workers --stream $n_workers --io $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}.log 2>&1 &"
                ;;
        esac

        echo "Waiting 3s for stress-ng to ramp up..."
        sleep 3

        # Verify stress-ng is running
        local stressng_check=$(run_ssh "pgrep -f 'stress-ng' | wc -l" 2>/dev/null || echo "0")
        if [[ "$stressng_check" -gt 0 ]]; then
            echo "✓ stress-ng verified running (processes: $stressng_check)"
        else
            echo "⚠ WARNING: stress-ng may not be running. Check logs."
        fi
    fi


    for run in $(seq 1 "$RUNS_PER_EXPERIMENT"); do
        local run_file="$exp_dir/run_$(printf '%02d' "$run").txt"
        local run_label="[$name] Run $run/$RUNS_PER_EXPERIMENT"

        echo -n "$run_label ... "

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        if [[ $HICOM_MODE -eq 1 ]]; then
            # Wait for the "HICOM_PING_DONE run=N" marker to ensure the run has fully completed
            if ! wait_for_hi_done "$qemu_log" "$run"; then
                echo "ERROR: Run $run did not complete successfully. Check console log."
                continue
            fi

            # Extract the console output for this run into the run file (overwriting the previous client-only output)
            extract_run_results "$qemu_log" "$run" "$run_file"
            
        fi

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

    # Retrieve stress-ng log before stopping QEMU
    if [[ $n_workers -gt 0 ]]; then
        echo "Retrieving stress-ng log..."
        sshpass -p "$SSH_PASS" scp $SSH_OPTS -P "$SSH_PORT" "$SSH_USER@$SSH_HOST:/tmp/stressng_${name}.log" "$exp_dir/stressng.log" 2>/dev/null || true
    fi

    # Stop stress-ng before tearing down QEMU.
    if [[ $n_workers -gt 0 ]]; then
        echo "Stopping stress-ng..."
        run_ssh "killall stress-ng 2>/dev/null || true"
        sleep 1
    fi

    stop_qemu
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    local all_conditions=()
    local conditions_to_run=()

    build_conditions all_conditions
    filter_conditions all_conditions conditions_to_run "$SINGLE_STRESSOR"

    echo "========================================================"
    echo "Scenario 2 Experiment Orchestrator"
    echo "Inter-VM Communication Latency and Reliability"
    echo "========================================================"
    echo "Runs per experiment:   $RUNS_PER_EXPERIMENT"
    echo "Pings per run:       $PINGS_PER_RUN"
    echo "Results directory:   $RESULTS_DIR"
    echo ""

    check_dependencies

    if [[ -n "$SINGLE_STRESSOR" ]]; then
        local valid_stressor=0
        for stressor in "${STRESSOR_TYPES[@]}"; do
            if [[ "$stressor" == "$SINGLE_STRESSOR" ]]; then
                valid_stressor=1
                break
            fi
        done
        if [[ $valid_stressor -ne 1 ]]; then
            echo "ERROR: Unknown stressor '$SINGLE_STRESSOR'"
            echo "Available: ${STRESSOR_TYPES[*]}"
            exit 1
        fi
    fi

    local total_runs=$(( ${#conditions_to_run[@]} * RUNS_PER_EXPERIMENT ))


    # Each run takes ~(n_messages * RTL + 2s ramp + 2s pause) ≈ 10-30s
    # Plus ~3 min boot overhead per condition
    local est_minutes=$(( (total_runs * 30 + ${#conditions_to_run[@]} * 180) / 60 ))
    echo "Total runs:          $total_runs across ${#conditions_to_run[@]} conditions"
    echo "Estimated time:      ~${est_minutes} minutes"
    echo "QEMU restarts:       once per condition"
    echo ""

    for condition in "${conditions_to_run[@]}"; do
        run_experiment "$condition"
    done

    echo ""
    echo "========================================================"
    echo "All experiments completed."
    echo "Results saved to: $RESULTS_DIR"
    echo "========================================================"
    echo ""
    echo "Next: python3 experiments/analyze_scenario2.py"
    python3 s2_plots.py -r "$RESULTS_DIR"
}

main
