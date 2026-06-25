#!/bin/bash
#
# Scenario 3 Experiment Orchestrator — Network Communication Determinism
#
# Runs experiments based on a matrix:
# Stressor types: baseline, cpu, cache, stream, io, worst-case
# Number of workers: 0 (baseline), 4, 8, 16, 32, 64


# QEMU is rebooted between conditions for a clean state.
#
# Before each condition the script:
#   1. Detects which vm-li interface is the vnet_li_hi (no 192.168.7.x IP).
#   2. Assigns 10.0.1.2/24 to that interface.
#   3. Optionally applies a tc netem rule (secondary conditions).
#
# Usage:
#   ./experiments/s3_orchestrator.sh                      # all conditions
#   ./experiments/s3_orchestrator.sh -t                   # use TCP protocol (UDP by default)
#   ./experiments/s3_orchestrator.sh -n 5                 # 5 runs per condition
#   ./experiments/s3_orchestrator.sh -m 1000              # 1000 packets per run
#   ./experiments/s3_orchestrator.sh -c C0_baseline       # single condition
#   ./experiments/s3_orchestrator.sh -b                   # run with hicom variant settings

set -o pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"

# Results root is chosen after parsing args so -b and -t can switch it
RESULTS_UDP_ROOT="$SCRIPT_DIR/results_udp" # no -b and -t
RESULTS_TCP_ROOT="$SCRIPT_DIR/results_tcp" # -t 
RESULTS_HICOM_UDP_ROOT="$SCRIPT_DIR/results_hicom_udp" # -b
RESULTS_HICOM_TCP_ROOT="$SCRIPT_DIR/results_hicom_tcp" # -b and -t

RUNS_PER_EXPERIMENT=30
PACKETS_PER_RUN=2000
SINGLE_STRESSOR=""
HICOM_MODE=0
TCP_MODE=0

# Marker strings from shm_ping_hi console output
READY_MARKER="HI_NET_PING_READY"
DONE_MARKER_PREFIX="HI_NET_PING_DONE run="
READY_TIMEOUT=60   # seconds to wait for HI_NET_PING_READY after boot

CLIENT_BIN="net_client"

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=60
SSH_TIMEOUT=60

# IP assigned to the vnet_li_hi interface on vm-li for this scenario.
VNET_LI_IP="10.0.1.2"
VNET_NETMASK="24"

STRESSOR_TYPES=(worst-case)
NUM_WORKERS_LIST=(n8)

declare -A NUM_WORKERS_MAP=([n2]=2 [n4]=4 [n8]=8)

# Condition-level mappings (populated below)
declare -A STRESSOR_WORKERS
declare -A STRESSOR_LABELS

normalize_QEMU_serial_output() {
    local to_csv="${1:-0}"
    awk -v TO_CSV="$to_csv" '
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

        if (TO_CSV == 1 && line !~ /,/) {
            gsub(/[[:space:]]+/, ",", line)
        }

        # Remove trailing commas
        sub(/,+$/, "", line)

        if (line != "") print line
    }'
}

fix_QEMU_serial_log_format() {
    normalize_QEMU_serial_output 0
}

normalize_vmstat_to_csv() {
    normalize_QEMU_serial_output 1
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
        -p|--packets)    PACKETS_PER_RUN="$2";    shift 2 ;;
        -s|--condition)  SINGLE_STRESSOR="$2";    shift 2 ;;
        -b|--hicom)      HICOM_MODE=1; CLIENT_BIN="net_trigger"; shift 1 ;;
        -t|--tcp)        TCP_MODE=1; shift 1 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Runs per experiment (default: 10)"
            echo "  -p, --packets <N>       Packets per run (default: 2000)"
            echo "  -s, --stressor <name>   Run only this stressor"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Primary conditions:  ${STRESSOR_TYPES[*]}"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Choose results dir based on mode

if [[ $HICOM_MODE -eq 1 && $TCP_MODE -eq 1 ]]; then
    RESULTS_DIR="$RESULTS_HICOM_TCP_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
elif [[ $HICOM_MODE -eq 1 ]]; then
    RESULTS_DIR="$RESULTS_HICOM_UDP_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
elif [[ $TCP_MODE -eq 1 ]]; then
    RESULTS_DIR="$RESULTS_TCP_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
else
    RESULTS_DIR="$RESULTS_UDP_ROOT/$(date +%Y-%m-%d_%H:%M:%S)"
fi


# ─── SSH Helper ───────────────────────────────────────────────────────────────

run_ssh() {
    sshpass -p "$SSH_PASS" ssh $SSH_OPTS -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$@"
}

# ─── QEMU Lifecycle ───────────────────────────────────────────────────────────

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
        sleep 15; elapsed=$((elapsed + 15))
        if ! pgrep -f qemu-system-aarch64 &>/dev/null; then
            echo "ERROR: QEMU process died. Check $log_file."
            return 1
        fi
    done
    echo "ERROR: Boot timeout after ${BOOT_TIMEOUT}s."
    return 1
}

wait_for_ssh() {
    echo "Waiting for SSH (timeout: ${SSH_TIMEOUT}s)..."
    local elapsed=0
    while [[ $elapsed -lt $SSH_TIMEOUT ]]; do
        if run_ssh "echo ready" &>/dev/null; then
            echo "SSH is ready."
            return 0
        fi
        sleep 5; elapsed=$((elapsed + 5))
    done
    echo "ERROR: SSH timeout after ${SSH_TIMEOUT}s."
    return 1
}

stop_qemu() {
    echo "Stopping QEMU..."
    pkill -f qemu-system-aarch64 2>/dev/null && sleep 2
    if [[ -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
        kill "$QEMU_PID" 2>/dev/null
        wait "$QEMU_PID" 2>/dev/null
    fi
    pkill -9 -f qemu-system-aarch64 2>/dev/null
    QEMU_PID=""
    echo "QEMU stopped."
}
# Wait for hi_net_ping to finish initialising and print HI_NET_PING_READY.
wait_for_hi_ready() {
    local log_file="$1"
    echo "Waiting for HI_NET_PING_READY in hi console (timeout: ${READY_TIMEOUT}s)..."
    local elapsed=0
    while [[ $elapsed -lt $READY_TIMEOUT ]]; do
        if grep -q "$READY_MARKER" "$log_file" 2>/dev/null; then
            echo "hi_net_ping is ready."
            return 0
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    echo "ERROR: HI_NET_PING_READY not seen after ${READY_TIMEOUT}s."
    return 1
}
# Wait for "HI_NET_PING_DONE run=<run_idx>" to appear in the console log.
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

extract_run_results() {
    local log_file="$1"
    local run_idx="$2"
    local dest_file="$3"
    local cycles_file="$4"
    awk \
        "/HI_NET_PING_START run=${run_idx}/{found=1} \
         found{print} \
         /HI_NET_PING_DONE run=${run_idx}/{found=0}" \
        "$log_file" | fix_QEMU_serial_log_format > "$dest_file"
     awk \
        "/===CSV_START===/{in_csv=1; next} \
         /===CSV_END===/{in_csv=0} \
         in_csv{print}" \
        "$dest_file" > "$cycles_file"

}

trap stop_qemu EXIT

# ─── vnet_li_hi Interface Detection and Configuration ────────────────────────

configure_vnet_iface() {
    # Find the interface on vm-li that is NOT the SSH gateway interface
    # (i.e. not the one with a 192.168.7.x address).  That is vnet_li_hi.
    local iface
    iface=$(run_ssh "
        for i in \$(ls /sys/class/net/); do
            [ \"\$i\" = lo ] && continue
            if ! ip -4 addr show \"\$i\" 2>/dev/null | grep -q '192\.168\.7\.'; then
                echo \"\$i\"
                break
            fi
        done
    " 2>/dev/null | tr -d '\r\n')

    if [[ -z "$iface" ]]; then
        echo "WARNING: could not auto-detect vnet_li_hi interface — trying eth1"
        iface="eth1"
    fi

    echo "Configuring $iface as vnet_li_hi: ${VNET_LI_IP}/${VNET_NETMASK}"
    run_ssh "
        ip link set '$iface' up
        ip addr flush dev '$iface' 2>/dev/null || true
        ip addr add '${VNET_LI_IP}/${VNET_NETMASK}' dev '$iface'
    " 2>/dev/null

    # Brief check: ping vm-hi with a short timeout
    if run_ssh "ping -c 1 -W 2 10.0.1.1" &>/dev/null; then
        echo "Reachability check: vm-hi (10.0.1.1) is reachable via $iface."
    else
        echo "WARNING: vm-hi (10.0.1.1) not reachable — net_client may not be up yet."
    fi

    echo "$iface"   # return value
}

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

    # Build net_receiver command
    # local flood_arg=""
    # [[ "$mbps" -gt 0 ]] && flood_arg="-B $mbps"

    local cmd="$CLIENT_BIN -n $PACKETS_PER_RUN"


    echo "============================================"
    echo "Condition: $name  ($label)"
    echo "Command:   $cmd"
    echo "Runs:      $RUNS_PER_CONDITION"
    echo "============================================"

    start_qemu "$qemu_log"
    wait_for_boot "$qemu_log" || { stop_qemu; return 1; }
    wait_for_ssh             || { stop_qemu; return 1; }

    # Configure vnet_li_hi IP and wait for net_client to be fully up
    local vnet_iface
    vnet_iface=$(configure_vnet_iface)
    sleep 3   # net_client may still be initialising its socket

    if [[ $HICOM_MODE -eq 1 ]]; then
        if ! wait_for_hi_ready "$qemu_log"; then
            echo "ERROR: shm_ping_hi not ready for experiment $name. Skipping."
            stop_qemu
            return 1
        fi
    fi

    # Give shm_echo (PID 1 on vm-hi) a moment to register in proxyshm
    sleep 2

    if [[ $n_workers -gt 0 ]]; then
        echo "Starting stress-ng for stressor '$stressor' with $n_workers workers"
        local stress_timeout=$((100 * RUNS_PER_EXPERIMENT))  # seconds, enough for all runs to complete
        echo "Stress-ng timeout: ${stress_timeout}s"
        if [[ "$stressor" == "worst-case" ]]; then
            run_ssh "stress-ng --cpu $n_workers --cache $n_workers --stream $n_workers --io $n_workers --perf --timeout ${stress_timeout}s --metrics-brief --verbose >/tmp/stressng.log 2>&1 &"
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

        # vmstat collects CPU / memory / IO stats every second.
        # After li_app finishes, the vmstat output is saved between markers
        # AND as a separate file for independent analysis.
        local monitor_duration=7
        local vmstat_file="$exp_dir/run_$(printf '%02d' "$run")_vmstat.csv"
        run_ssh "{
              vmstat -n 1 $monitor_duration > /tmp/vmstat_out.txt 2>&1 &
              VPID=\$!
              $cmd
              # Let vmstat exit naturally so buffered output is flushed before extraction.
              wait \$VPID 2>/dev/null || true
              sync
              echo '===VMSTAT_START===';
              cat /tmp/vmstat_out.txt;
              echo '===VMSTAT_END===';
              }" > "$run_file" 2>&1
        local exit_code=$?
        # Extract the vmstat output between the markers and save to a clean CSV file for analysis. If the file is empty after extraction, remove it.
        sed -n '/===VMSTAT_START===/,/===VMSTAT_END===/{/===VMSTAT/d;p}' "$run_file" | \
                normalize_vmstat_to_csv > "$vmstat_file"
            [[ -s "$vmstat_file" ]] || rm -f "$vmstat_file"
        
        if [[ $HICOM_MODE -eq 0 ]]; then
            sed -n '/===CSV_START===/,/===CSV_END===/{/===CSV/d;p}' "$run_file" | \
                    fix_QEMU_serial_log_format > "$cycles_file"
                [[ -s "$cycles_file" ]] || rm -f "$cycles_file"
        fi
        

        if [[ $HICOM_MODE -eq 1 ]]; then
            # Wait for the "HICOM_PING_DONE run=N" marker to ensure the run has fully completed
            if ! wait_for_hi_done "$qemu_log" "$run"; then
                echo "ERROR: Run $run did not complete successfully. Check console log."
                continue
            fi
            # Extract the run's console output between the HI_NET_PING_START and HI_NET_PING_DONE markers for analysis
            extract_run_results "$qemu_log" "$run" "$run_file" "$cycles_file"
        fi

        if [[ $exit_code -ne 0 && $exit_code -ne 1 ]]; then
            # exit 1 means messages were lost — still a valid run, just noted
            echo "FAILED (exit code: $exit_code)"
        else
            # Quick-feedback: extract key metrics from the output
            local lost
            local mean_rtl
            local p99_rtl
            lost=$(awk '/^Lost \(no echo\):/ {print $4; exit}' "$run_file" 2>/dev/null || echo "?")
            mean_rtl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            p99_rtl=$(grep -oP 'P99:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            if [[ "$exit_code" -eq 1 ]]; then
                echo "OK (lost: $lost, mean: ${mean_rtl} us, P99: ${p99_rtl} us) [LOSSES DETECTED]"
            fi
        fi

        if [[ $n_workers -gt 0 ]]; then
            echo "Stopping stress-ng for run $run..."
            run_ssh "killall stress-ng 2>/dev/null || true"
            sleep 1
        fi

        # Brief pause between runs — shm_echo remains running and ready
        sleep 2
    done

    stop_qemu
}



# ─── Preflight ────────────────────────────────────────────────────────────────

check_dependencies() {
    local missing=0
    for cmd in sshpass task qemu-system-aarch64; do
        if ! command -v "$cmd" &>/dev/null; then
            echo "ERROR: '$cmd' is not installed."
            missing=1
        fi
    done
    [[ $missing -eq 1 ]] && { echo "Install missing deps and retry."; exit 1; }
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    local all_conditions=()
    local conditions_to_run=()
    build_conditions all_conditions
    filter_conditions all_conditions conditions_to_run "$SINGLE_STRESSOR"

    echo "========================================================"
    echo "Scenario 3 Experiment Orchestrator"
    echo "Network Communication Determinism"
    echo "========================================================"
    echo "Runs per experiment:        $RUNS_PER_EXPERIMENT"
    echo "Packets per run:            $PACKETS_PER_RUN"
    echo "Results directory:          $RESULTS_DIR"
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
    python3 s3_plots.py -r "$RESULTS_DIR"
}

main
