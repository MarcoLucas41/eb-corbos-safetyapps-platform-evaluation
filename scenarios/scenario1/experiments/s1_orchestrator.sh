#!/bin/bash
#
# FFI Experiment Orchestrator
# Runs inside the dev container.
#
# Usage:
#   ./experiments/run_experiments.sh                    # Run all scenarios, 10 iterations each
#   ./experiments/run_experiments.sh -n 5               # 5 iterations per scenario
#   ./experiments/run_experiments.sh -d 30              # 30s per run instead of 60s
#   ./experiments/run_experiments.sh -s S0_baseline     # Run only one scenario
#

set -o pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results/$(date +%Y-%m-%d_%H:%M:%S)"

RUNS_PER_SCENARIO=10
DURATION=60
SINGLE_SCENARIO=""

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04.5 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=180   # seconds to wait for boot
SSH_TIMEOUT=120     # seconds to wait for SSH readiness

# ─── Scenario Definitions ───────────────────────────────────────────────────
# Order matters: use indexed arrays to preserve it.

SCENARIO_NAMES=(
    "S0_baseline"
    "S1_cpu"
    "S2_cache"
    "S3_stream"
    "S4_io"
    "S5_worst_case"
)

declare -A SCENARIO_PARAMS
SCENARIO_PARAMS[S0_baseline]=""
SCENARIO_PARAMS[S1_cpu]="-c 0"
SCENARIO_PARAMS[S2_cache]="-C 2"
SCENARIO_PARAMS[S3_stream]="-S 2"
SCENARIO_PARAMS[S4_io]="-i 2"
SCENARIO_PARAMS[S5_worst_case]="-c 0 -C 2 -S 2 -i 2"

# ─── Argument Parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--runs)    RUNS_PER_SCENARIO="$2"; shift 2 ;;
        -d|--duration) DURATION="$2"; shift 2 ;;
        -s|--scenario) SINGLE_SCENARIO="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Iterations per scenario (default: 10)"
            echo "  -d, --duration <sec>    Duration per run in seconds (default: 60)"
            echo "  -s, --scenario <name>   Run only this scenario (e.g. S0_baseline)"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Scenarios: ${SCENARIO_NAMES[*]}"
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
    sleep 5

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
        sleep 10
        elapsed=$((elapsed + 10))

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

run_scenario() {
    local name="$1"
    local params="${SCENARIO_PARAMS[$name]}"
    local scenario_dir="$RESULTS_DIR/$name"
    local scenario_log="$scenario_dir/qemu_serial.log"
    mkdir -p "$scenario_dir"

    # Build the stress_workload command
    local cmd="stress_workload $params -d $DURATION"

    echo ""
    echo "============================================"
    echo "Scenario: $name"
    echo "Command:  $cmd"
    echo "Runs:     $RUNS_PER_SCENARIO"
    echo "============================================"

    # Start fresh QEMU for this scenario
    start_qemu "$scenario_log"
    if ! wait_for_boot "$scenario_log"; then
        echo "ERROR: Failed to boot QEMU for scenario $name. Skipping."
        stop_qemu
        return 1
    fi
    if ! wait_for_ssh; then
        echo "ERROR: SSH not available for scenario $name. Skipping."
        stop_qemu
        return 1
    fi

    for run in $(seq 1 "$RUNS_PER_SCENARIO"); do
        local run_num=$(printf '%02d' "$run")
        local run_file="$scenario_dir/run_${run_num}.txt"
        local cycles_file="$scenario_dir/run_${run_num}_cycles.csv"
        local run_label="[$name] Run $run/$RUNS_PER_SCENARIO"

        echo -n "$run_label ... "

        # Track serial log length so we can extract new CSV data after the run
        local log_lines_before=$(wc -l < "$scenario_log" 2>/dev/null || echo 0)

        # Run the experiment via SSH and capture output
        # run_ssh "$cmd" > "$run_file" 2>&1
        # local exit_code=$?

         # Run li_app with vmstat monitoring in the background.
        # vmstat collects CPU / memory / IO stats every second.
        # After li_app finishes, the vmstat output is saved between markers
        # AND as a separate file for independent analysis.
        local monitor_duration=$((DURATION + 30))
        local vmstat_file="$scenario_dir/run_${run_num}_vmstat.csv"
        local speedometer_vmstat_file="$scenario_dir/run_${run_num}_speedometer_vmstat.csv"

        run_ssh "vmstat -n 1 $monitor_duration > /tmp/vmstat_out.txt 2>&1 &
                  VPID=\$!;
                  $cmd;
                  kill \$VPID 2>/dev/null; wait \$VPID 2>/dev/null;
                  sync;
                  echo '===VMSTAT_START===';
                  cat /tmp/vmstat_out.txt;
                  echo '===VMSTAT_END==='" > "$run_file" 2>&1
        local exit_code=$?

         # Save LI-side vmstat as real CSV (comma-separated)
         if [[ -f "$run_file" ]]; then
            sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$run_file" \
                | normalize_vmstat_to_csv > "$vmstat_file"
            [[ -s "$vmstat_file" ]] || rm -f "$vmstat_file"
        fi


        # Wait for QEMU serial log to flush before extracting per-cycle CSV.
        # The hi VM dumps CSV via the serial port; QEMU may buffer the output.
        sleep 5

        # Extract per-cycle CSV + speedometer vmstat CSV from new serial-log lines
        if [[ -f "$scenario_log" ]]; then
            local log_chunk
            log_chunk="$(mktemp)"

            tail -n +$((log_lines_before + 1)) "$scenario_log" 2>/dev/null > "$log_chunk"

            sed -n '/===CSV_START===/,/===CSV_END===/{/===CSV/d;p}' "$log_chunk" \ | normalize_vmstat_to_csv > "$cycles_file"
            [[ -s "$cycles_file" ]] || rm -f "$cycles_file"

            sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$log_chunk" \
                | normalize_vmstat_to_csv > "$speedometer_vmstat_file"
            [[ -s "$speedometer_vmstat_file" ]] || rm -f "$speedometer_vmstat_file"

            rm -f "$log_chunk"
        fi

        if [[ $exit_code -ne 0 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            # Extract key metrics for quick feedback
            local misses=$(grep -oP 'Deadline misses: \K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            echo "OK (misses: $misses)"
        fi

        # Brief pause between runs to let the speedometer reset
        sleep 2
    done

    # Shut down QEMU after this scenario
    stop_qemu
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    echo "========================================================"
    echo "FFI Experiment Orchestrator"
    echo "========================================================"
    echo "Runs per scenario: $RUNS_PER_SCENARIO"
    echo "Duration per run:  ${DURATION}s"
    echo "Results directory:  $RESULTS_DIR"
    echo ""

    check_dependencies

    # Determine which scenarios to run
    local scenarios_to_run=()
    if [[ -n "$SINGLE_SCENARIO" ]]; then
        # Validate scenario name
        if [[ -z "${SCENARIO_PARAMS[$SINGLE_SCENARIO]+x}" ]]; then
            echo "ERROR: Unknown scenario '$SINGLE_SCENARIO'"
            echo "Available: ${SCENARIO_NAMES[*]}"
            exit 1
        fi
        scenarios_to_run=("$SINGLE_SCENARIO")
    else
        scenarios_to_run=("${SCENARIO_NAMES[@]}")
    fi

    local total_runs=$(( ${#scenarios_to_run[@]} * RUNS_PER_SCENARIO ))
    # Add ~3 min boot overhead per scenario
    local est_minutes=$(( (total_runs * (DURATION + 10) + ${#scenarios_to_run[@]} * 180) / 60 ))
    echo ""
    echo "Total runs: $total_runs across ${#scenarios_to_run[@]} scenarios"
    echo "QEMU will restart between scenarios for clean state."
    echo "Estimated time: ~${est_minutes} minutes"
    echo ""

    for scenario in "${scenarios_to_run[@]}"; do
        run_scenario "$scenario"
    done

    echo ""
    echo "========================================================"
    echo "All experiments completed."
    echo "Results saved to: $RESULTS_DIR"
    echo "========================================================"
    echo ""
    echo "Next: run 'python3 experiments/analyze_results.py' to generate summary plots."
    echo "Generating deadline miss plots..."
    python3 plot_deadline_misses.py "$RESULTS_DIR" --all
    python3 s1_plots.py -r "$RESULTS_DIR"
}

main
