#!/bin/bash
#
# Scenario 1 Experiment Orchestrator — Freedom from Interference
#
# Runs experiments based on a matrix:
# Speedometer update periods: 2 Hz, 10 Hz, 20 Hz (default: 20 Hz)
# Deadline miss thresholds: 10ms
# Stressor types: baseline, cpu, cache, stream, io, worst-case
# Number of workers: 0 (baseline), 4, 8, 16, 32, 64
#
# Usage:
#   ./experiments/s1_orchestrator.sh                    # Run all scenarios, 10 iterations each
#   ./experiments/s1_orchestrator.sh -b                 # run with variant B (li-speedometer) settings
#   ./experiments/s1_orchestrator.sh -n 5               # 5 iterations per scenario
#   ./experiments/s1_orchestrator.sh -d 30              # 30s per run instead of 60s
#   ./experiments/s1_orchestrator.sh -s S0_baseline     # Run only one scenario
#   ./experiments/s1_orchestrator.sh -hz 5              # Set speedometer to 5hz
#   ./experiments/s1_orchestrator.sh -dm 20             # Set deadline miss threshold to 20ms
#

set -o pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"

# Results root is chosen after parsing args so -b can switch it
RESULTS_ROOT="$SCRIPT_DIR/results"
RESULTS_LI_ROOT="$SCRIPT_DIR/results_li"

RUNS_PER_EXPERIMENT=30
DURATION=15
DEADLINE_MISS_THRESHOLD_MS=5
SPEEDOMETER_UPDATE_HZ=20
SINGLE_STRESSOR=""
LI_MODE=0

CLIENT_BIN="speedometer_trigger"

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=60   # seconds to wait for boot
SSH_TIMEOUT=60    # seconds to wait for SSH readiness

STRESSOR_TYPES=(baseline cpu cache stream io worst-case)
NUM_WORKERS_LIST=(n2 n4 n8)

declare -A NUM_WORKERS_MAP=( [n2]=2 [n4]=4 [n8]=8 )

# Condition-level mappings (populated below)
declare -A STRESSOR_WORKERS
declare -A STRESSOR_LABELS

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
        -n|--runs)     RUNS_PER_EXPERIMENT="$2"; shift 2 ;;
        -d|--duration) DURATION="$2"; shift 2 ;;
        -b|--limode)   LI_MODE=1; CLIENT_BIN="li_speedometer"; shift 1 ;;
        -dm|--deadline-ms) DEADLINE_MISS_THRESHOLD_MS="$2"; shift 2 ;;
        -hz|--speedometer-hz) SPEEDOMETER_UPDATE_HZ="$2"; shift 2 ;;
        -s|--stressor) SINGLE_STRESSOR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Iterations per scenario (default: 10)"
            echo "  -d, --duration <sec>    Duration per run in seconds (default: 60)"
            echo "  -b, --limode            Use LI-side speedometer variant (results go to $RESULTS_LI_ROOT)"
            echo "  -dm, --deadline-ms <ms> Set deadline miss threshold in ms (default: 10)"
            echo "  -hz, --speedometer-hz <hz> Set speedometer update frequency (default: 20)"
            echo "  -s, --stressor <name>   Run only this stressor (e.g. S0_baseline)"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Stressors: ${STRESSOR_TYPES[*]}"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Choose results dir based on mode
if [[ $LI_MODE -eq 1 ]]; then
    RESULTS_DIR="$RESULTS_LI_ROOT/${SPEEDOMETER_UPDATE_HZ}hz/$(date +%Y-%m-%d_%H:%M:%S)"
else
    RESULTS_DIR="$RESULTS_ROOT/${SPEEDOMETER_UPDATE_HZ}hz/$(date +%Y-%m-%d_%H:%M:%S)"
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
# Normalize vmstat text into clean CSV:
# - remove ANSI escape codes
# - remove serial prefix like "vm-hi,|," or "vm-hi |"
# - convert whitespace-delimited vmstat rows to comma-delimited
# - drop trailing commas
normalize_vmstat_to_csv() {
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

        # If not already comma-separated, convert runs of spaces/tabs to commas
        if (line !~ /,/) {
            gsub(/[[:space:]]+/, ",", line)
        }

        # Remove trailing commas
        sub(/,+$/, "", line)

        if (line != "") print line
    }'
}

# ─── QEMU Lifecycle ─────────────────────────────────────────────────────────

QEMU_PID=""

start_qemu() {
    local log_file="$1"
    echo "Starting QEMU..."
    mkdir -p "$RESULTS_DIR"

    # Run task qemu in background, capture serial output
    (cd "$PROJECT_DIR" && task qemu) > "$log_file" 2>&1 &
    QEMU_PID=$!

    # Wait briefly for qemu-system-aarch64 to spawn
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

        # Check that a QEMU process is still running
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

    # Kill the actual qemu-system-aarch64 process(es)
    if pkill -f qemu-system-aarch64 2>/dev/null; then
        sleep 2
    fi

    # Also kill the task/shell wrapper if still alive
    if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null
        wait "$QEMU_PID" 2>/dev/null
    fi

    # Ensure nothing is left
    pkill -9 -f qemu-system-aarch64 2>/dev/null

    QEMU_PID=""
    echo "QEMU stopped."
}

# Ensure cleanup on exit
trap stop_qemu EXIT

# ─── Run Experiments ─────────────────────────────────────────────────────────

run_experiment() {

    local name="$1"
    local exp_dir="$RESULTS_DIR/$name"
    local qemu_log="$exp_dir/qemu_serial.log"
    mkdir -p "$exp_dir"

     # name is like "cpu_n4" or "baseline_n0"
    local stressor="${name%%_*}"
    local n_workers=${STRESSOR_WORKERS[$name]:-0}
    local label="${STRESSOR_LABELS[$name]:-N=$n_workers}"

    # Build the speedometer_trigger command
    local cmd="$CLIENT_BIN -d $DURATION -dm $DEADLINE_MISS_THRESHOLD_MS -hz $SPEEDOMETER_UPDATE_HZ"

    echo "============================================"
    echo "Experiment: $name"
    echo "Command:  $cmd"
    echo "Runs:     $RUNS_PER_EXPERIMENT"
    echo "============================================"

    # Start fresh QEMU for this scenario
    start_qemu "$qemu_log"
    if ! wait_for_boot "$qemu_log"; then
        echo "ERROR: Failed to boot QEMU for scenario $name. Skipping."
        stop_qemu
        return 1
    fi
    if ! wait_for_ssh; then
        echo "ERROR: SSH not available for scenario $name. Skipping."
        stop_qemu
        return 1
    fi

    if [[ $n_workers -gt 0 ]]; then
        echo "Starting stress-ng for stressor '$stressor' with $n_workers workers"
        local stress_timeout=$((100 * RUNS_PER_EXPERIMENT))  # seconds, enough for all runs to complete
        echo "Stress-ng timeout: ${stress_timeout}s"
        if [[ "$stressor" == "worst-case" ]]; then
            run_ssh "stress-ng --cpu $n_workers --cache $n_workers --stream $n_workers --io $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}_run_$(printf '%02d' "$run").log 2>&1 &"
        fi
        run_ssh "stress-ng --${stressor} $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng_${name}_run_$(printf '%02d' "$run").log 2>&1 &"
    

        echo "Waiting for stress-ng to become active..."
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
        local cycles_file="$exp_dir/run_$(printf '%02d' "$run").csv"
        local run_label="[$name] Run $run/$RUNS_PER_EXPERIMENT"


        echo -n "$run_label ... "

        # Track serial log length so we can extract new CSV data after the run
        local log_lines_before=$(wc -l < "$qemu_log" 2>/dev/null || echo 0)

        # Run low integrity app with vmstat monitoring in the background.

        # vmstat collects CPU / memory / IO stats every second.
        # After low-integrity app finishes, the vmstat output is saved between markers
        # AND as a separate file for independent analysis.
        local monitor_duration=$((DURATION + 2)) # Slightly longer than app duration to capture tail-end behavior
        local vmstat_file="$exp_dir/run_$(printf '%02d' "$run")_vmstat.csv"

        # local speedometer_vmstat_file="$exp_dir/run_$(printf '%02d' "$run")_speedometer_vmstat.csv"

        run_ssh "{
              vmstat -n 1 "$monitor_duration" > /tmp/vmstat_out.txt 2>&1 &
              VPID=\$!
              $cmd
              wait \$VPID 2>/dev/null
              sync
              echo '===VMSTAT_START===';
              cat /tmp/vmstat_out.txt;
              echo '===VMSTAT_END===';
              }" > "$run_file" 2>&1
        local exit_code=$?

         # Save LI-side vmstat and cycles data as CSV (comma-separated)
        if [[ $LI_MODE -eq 1 && -f "$run_file" ]]; then
            sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$run_file" | \
                normalize_vmstat_to_csv > "$vmstat_file"
            [[ -s "$vmstat_file" ]] || rm -f "$vmstat_file"
             sed -n '/===CSV_START===/,/===CSV_END===/{/===CSV/d;p}' "$run_file" | \
                normalize_vmstat_to_csv > "$cycles_file"
            [[ -s "$cycles_file" ]] || rm -f "$cycles_file"
        fi

        if [[ $LI_MODE -eq 0 && -f "$run_file" ]]; then
           
            # Wait for QEMU serial log to flush before extracting per-cycle CSV.
            # The hi VM dumps CSV via the serial port; QEMU may buffer the output.
            sleep 1

            local log_chunk
            local clean_chunk
            log_chunk="$(mktemp)"
            clean_chunk="$(mktemp)"

            tail -n +$((log_lines_before + 1)) "$qemu_log" 2>/dev/null > "$log_chunk"
            perl -pe 's/\e\[[0-9;]*[[:alpha:]]//g; s/^.*vm-hi[[:space:]]*\|[[:space:]]*//; s/\r$//' "$log_chunk" > "$clean_chunk"

            # Append the speedometer statistics block to the per-run text log.
            sed -n '/^========== SPEEDOMETER RT APPLICATION RESULTS ==========/,/^========================================================$/p' "$clean_chunk" >> "$run_file"

            sed -n '/===CSV_START===/,/===CSV_END===/{/===CSV/d;p}' "$clean_chunk" | \
                normalize_vmstat_to_csv > "$cycles_file"
            [[ -s "$cycles_file" ]] || rm -f "$cycles_file"

            sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$run_file" | \
                normalize_vmstat_to_csv > "$vmstat_file"
            [[ -s "$vmstat_file" ]] || rm -f "$vmstat_file"

            # sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$clean_chunk" | \
            #     normalize_vmstat_to_csv > "$speedometer_vmstat_file"
            # [[ -s "$speedometer_vmstat_file" ]] || rm -f "$speedometer_vmstat_file"

            rm -f "$clean_chunk"
            rm -f "$log_chunk"
        fi

        if [[ $exit_code -ne 0 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            # Extract key metrics for quick feedback
            local misses=$(grep -oP 'Deadline misses: \K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            echo "OK (misses: $misses)"
        fi

    done

    # Retrieve stress-ng log before stopping QEMU
    if [[ $n_workers -gt 0 ]]; then
        echo "Retrieving stress-ng log..."
        sshpass -p "$SSH_PASS" scp $SSH_OPTS -P "$SSH_PORT" "$SSH_USER@$SSH_HOST:/tmp/stressng_${name}.log" "$exp_dir/stressng.log" 2>/dev/null || true
        echo "Stopping stress-ng..."
        run_ssh "killall stress-ng 2>/dev/null || true"
    fi

    # Shut down QEMU after this scenario
    stop_qemu
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    local all_conditions=()
    local conditions_to_run=()

    build_conditions all_conditions
    filter_conditions all_conditions conditions_to_run "$SINGLE_STRESSOR"

    echo "========================================================"
    echo "Scenario 1 Experiment Orchestrator"
    echo "Freedom from Interference"
    echo "========================================================"
    echo "Runs per experiment: $RUNS_PER_EXPERIMENT"
    echo "Duration per run:  ${DURATION}s"
    echo "Results directory:  $RESULTS_DIR"
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

    # Add ~1 min boot overhead per scenario
    local est_minutes=$(( (total_runs * (DURATION) + ${#conditions_to_run[@]} * 60) / 60 ))
    echo ""
    echo "Total runs: $total_runs across ${#conditions_to_run[@]} scenarios"
    echo "QEMU will restart between scenarios for clean state."
    echo "Estimated time: ~${est_minutes} minutes"
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
    # python3 s1_plots.py -r "$RESULTS_DIR"
}

main
