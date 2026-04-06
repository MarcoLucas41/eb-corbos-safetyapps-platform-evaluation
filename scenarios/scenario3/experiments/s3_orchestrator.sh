#!/bin/bash
#
# Scenario 3 Experiment Orchestrator — Network Communication Determinism
#
# Runs 6 primary conditions (C0–C5, flood N = 0/16/32/64/128/256 Mbps)
# and 4 secondary netem conditions (D1/D2/L1/L2), via SSH into vm-li.
# QEMU is rebooted between conditions for a clean state.
#
# Before each condition the script:
#   1. Detects which vm-li interface is the vnet_li_hi (no 192.168.7.x IP).
#   2. Assigns 10.0.1.2/24 to that interface.
#   3. Optionally applies a tc netem rule (secondary conditions).
#
# Usage:
#   ./experiments/run_scenario3.sh                      # all conditions
#   ./experiments/run_scenario3.sh -n 5                 # 5 runs per condition
#   ./experiments/run_scenario3.sh -m 1000              # 1000 packets per run
#   ./experiments/run_scenario3.sh -c C0_baseline       # single condition
#   ./experiments/run_scenario3.sh -c D1_delay_1ms      # single netem condition
#

set -o pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results_udp/$(date +%Y-%m-%d_%H:%M:%S)"

RUNS_PER_CONDITION=10
PACKETS_PER_RUN=10000
SINGLE_CONDITION=""

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
TSHARK_AVAILABLE=""

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=180
SSH_TIMEOUT=120

# IP assigned to the vnet_li_hi interface on vm-li for this scenario.
VNET_LI_IP="10.0.1.2"
VNET_NETMASK="24"

# ─── Primary Condition Definitions ───────────────────────────────────────────
# Each condition passes -B <Mbps> to net_receiver; C0 passes nothing (baseline).

PRIMARY_NAMES=(
    "C0_baseline"
    "C1_16mbps"
    "C2_32mbps"
    "C3_64mbps"
    "C4_128mbps"
    "C5_256mbps"
)

declare -A PRIMARY_FLOOD_MBPS
PRIMARY_FLOOD_MBPS[C0_baseline]="0"
PRIMARY_FLOOD_MBPS[C1_16mbps]="16"
PRIMARY_FLOOD_MBPS[C2_32mbps]="32"
PRIMARY_FLOOD_MBPS[C3_64mbps]="64"
PRIMARY_FLOOD_MBPS[C4_128mbps]="128"
PRIMARY_FLOOD_MBPS[C5_256mbps]="256"

declare -A PRIMARY_LABELS
PRIMARY_LABELS[C0_baseline]="N=0 Mbps (baseline)"
PRIMARY_LABELS[C1_16mbps]="N=16 Mbps"
PRIMARY_LABELS[C2_32mbps]="N=32 Mbps"
PRIMARY_LABELS[C3_64mbps]="N=64 Mbps"
PRIMARY_LABELS[C4_128mbps]="N=128 Mbps"
PRIMARY_LABELS[C5_256mbps]="N=256 Mbps"

# ─── Secondary (netem) Condition Definitions ─────────────────────────────────
# Runs with N=0 flood; a tc netem rule is applied on the vnet_li_hi interface.

NETEM_NAMES=(
    "D1_delay_1ms"
    "D2_delay_5ms_jitter"
    "L1_loss_0p5"
    "L2_loss_2p0"
)

declare -A NETEM_PARAMS
NETEM_PARAMS[D1_delay_1ms]="delay 1ms"
NETEM_PARAMS[D2_delay_5ms_jitter]="delay 5ms 1ms distribution normal"
NETEM_PARAMS[L1_loss_0p5]="loss 0.5%"
NETEM_PARAMS[L2_loss_2p0]="loss 2%"

declare -A NETEM_LABELS
NETEM_LABELS[D1_delay_1ms]="delay 10000ms"
NETEM_LABELS[D2_delay_5ms_jitter]="delay 20ms 10ms"
NETEM_LABELS[L1_loss_0p5]="loss 20%"
NETEM_LABELS[L2_loss_2p0]="loss random 99%"

NETEM_RUNS=5      # fewer runs for the secondary block
NETEM_PACKETS=1000

# ─── Argument Parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--runs)       RUNS_PER_CONDITION="$2"; shift 2 ;;
        -m|--messages)   PACKETS_PER_RUN="$2";    shift 2 ;;
        -c|--condition)  SINGLE_CONDITION="$2";    shift 2 ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "  -n, --runs <N>          Runs per condition (default: 10)"
            echo "  -m, --messages <N>      Packets per run (default: 5000)"
            echo "  -c, --condition <name>  Run only this condition"
            echo "  -h, --help              Show this help"
            echo ""
            echo "Primary conditions:  ${PRIMARY_NAMES[*]}"
            echo "Netem conditions:    ${NETEM_NAMES[*]}"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── SSH Helper ───────────────────────────────────────────────────────────────

run_ssh() {
    sshpass -p "$SSH_PASS" ssh $SSH_OPTS -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$@"
}

# ─── Remote tshark helpers ───────────────────────────────────────────────────

detect_remote_tshark() {
    if [[ -n "$TSHARK_AVAILABLE" ]]; then
        return
    fi

    if run_ssh "command -v tshark >/dev/null 2>&1"; then
        TSHARK_AVAILABLE="1"
        echo "Remote tshark is available."
    else
        TSHARK_AVAILABLE="0"
        echo "WARNING: tshark not found in VM; packet capture logs will be skipped."
    fi
}

start_tshark_capture() {
    local iface="$1"
    local remote_file="$2"

    if [[ "$TSHARK_AVAILABLE" != "1" ]]; then
        echo ""
        return 0
    fi

    run_ssh "rm -f '$remote_file'; nohup tshark -i '$iface' -n -l > '$remote_file' 2>&1 < /dev/null & echo \$!" 2>/dev/null | tr -d '\r\n'
}

stop_tshark_capture() {
    local pid="$1"

    if [[ "$TSHARK_AVAILABLE" != "1" || -z "$pid" ]]; then
        return 0
    fi

    # SIGINT gives tshark a chance to flush output before we collect the log.
    run_ssh "kill -2 '$pid' 2>/dev/null || true" >/dev/null 2>&1 || true
    sleep 1
}

save_tshark_capture() {
    local remote_file="$1"
    local local_file="$2"

    if [[ "$TSHARK_AVAILABLE" != "1" ]]; then
        return 0
    fi

    run_ssh "cat '$remote_file' 2>/dev/null || true" > "$local_file"
    run_ssh "rm -f '$remote_file'" >/dev/null 2>&1 || true
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
        sleep 5; elapsed=$((elapsed + 5))
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
        echo "WARNING: vm-hi (10.0.1.1) not reachable — net_sender may not be up yet."
    fi

    echo "$iface"   # return value
}

# ─── Apply / Remove netem ─────────────────────────────────────────────────────

apply_netem() {
    local iface="$1"
    local params="$2"
    echo "Running command: tc qdisc add dev '$iface' root netem $params"
    # run_ssh "tc qdisc add dev '$iface' root netem $params" 2>/dev/null || \
    #     echo "WARNING: tc netem apply failed (iproute2 may not be installed)"
    run_ssh "tc qdisc add dev eth0 root netem $params" 2>/dev/null || \
        echo "WARNING: tc netem apply failed (iproute2 may not be installed)"
}

remove_netem() {
    local iface="$1"
    run_ssh "tc qdisc del dev '$iface' root 2>/dev/null || true"
}

# ─── Run a Single Primary Condition ──────────────────────────────────────────

run_primary_condition() {
    local name="$1"
    local mbps="${PRIMARY_FLOOD_MBPS[$name]}"
    local label="${PRIMARY_LABELS[$name]}"
    local cond_dir="$RESULTS_DIR/$name"
    local qemu_log="$cond_dir/qemu_serial.log"
    mkdir -p "$cond_dir"

    # Build net_receiver command
    local flood_arg=""
    [[ "$mbps" -gt 0 ]] && flood_arg="-B $mbps"
    local cmd="net_receiver -n $PACKETS_PER_RUN $flood_arg"

    echo ""
    echo "============================================"
    echo "Condition: $name  ($label)"
    echo "Command:   $cmd"
    echo "Runs:      $RUNS_PER_CONDITION"
    echo "============================================"

    start_qemu "$qemu_log"
    wait_for_boot "$qemu_log" || { stop_qemu; return 1; }
    wait_for_ssh             || { stop_qemu; return 1; }
    detect_remote_tshark

    # Configure vnet_li_hi IP and wait for net_sender to be fully up
    local vnet_iface
    vnet_iface=$(configure_vnet_iface)
    sleep 3   # net_sender may still be initialising its socket

    for run in $(seq 1 "$RUNS_PER_CONDITION"); do
        local run_file="$cond_dir/run_$(printf '%02d' "$run").txt"
        local tshark_file="$cond_dir/tshark_run_$(printf '%02d' "$run").log"
        local tshark_remote_file="/tmp/tshark_${name}_run_$(printf '%02d' "$run").log"
        local tshark_pid=""
        echo -n "[$name] Run $run/$RUNS_PER_CONDITION ... "

        tshark_pid=$(start_tshark_capture "eth0" "$tshark_remote_file")
        [[ -n "$tshark_pid" ]] && sleep 1

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        stop_tshark_capture "$tshark_pid"
        save_tshark_capture "$tshark_remote_file" "$tshark_file"

        if [[ $exit_code -gt 1 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            local lost mean_owl p99_owl
            lost=$(grep -oP 'Packets lost:\s+\K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            mean_owl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            p99_owl=$(grep -oP 'P99:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            local flag=""
            [[ "$exit_code" -eq 1 ]] && flag=" [LOSSES]"
            echo "OK (lost: $lost, OWL mean: ${mean_owl} us, P99: ${p99_owl} us)${flag}"
        fi

        sleep 2   # brief pause — net_sender keeps running between runs
    done

    stop_qemu
}

# ─── Run a Single Netem Condition ────────────────────────────────────────────

run_netem_condition() {
    local name="$1"
    local netem_params="${NETEM_PARAMS[$name]}"
    local label="${NETEM_LABELS[$name]}"
    local cond_dir="$RESULTS_DIR/$name"
    local qemu_log="$cond_dir/qemu_serial.log"
    mkdir -p "$cond_dir"

    local cmd="net_receiver -n $NETEM_PACKETS"

    echo ""
    echo "============================================"
    echo "Netem condition: $name  ($label)"
    echo "Command:         $cmd"
    echo "Runs:            $NETEM_RUNS"
    echo "============================================"

    start_qemu "$qemu_log"
    wait_for_boot "$qemu_log" || { stop_qemu; return 1; }
    wait_for_ssh             || { stop_qemu; return 1; }
    detect_remote_tshark

    local vnet_iface
    vnet_iface=$(configure_vnet_iface)
    sleep 3

    # Apply netem rule for this condition
    apply_netem "$vnet_iface" "$netem_params"

    for run in $(seq 1 "$NETEM_RUNS"); do
        local run_file="$cond_dir/run_$(printf '%02d' "$run").txt"
        local tshark_file="$cond_dir/tshark_run_$(printf '%02d' "$run").log"
        local tshark_remote_file="/tmp/tshark_${name}_run_$(printf '%02d' "$run").log"
        local tshark_pid=""
        echo -n "[$name] Run $run/$NETEM_RUNS ... "

        tshark_pid=$(start_tshark_capture "$vnet_iface" "$tshark_remote_file")
        [[ -n "$tshark_pid" ]] && sleep 1

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        stop_tshark_capture "$tshark_pid"
        save_tshark_capture "$tshark_remote_file" "$tshark_file"

        if [[ $exit_code -gt 1 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            local lost mean_owl
            lost=$(grep -oP 'Packets lost:\s+\K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            mean_owl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            local flag=""
            [[ "$exit_code" -eq 1 ]] && flag=" [LOSSES]"
            echo "OK (lost: $lost, OWL mean: ${mean_owl} us)${flag}"
        fi

        sleep 2
    done

    remove_netem "$vnet_iface"
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
    echo "========================================================"
    echo "Scenario 3 Experiment Orchestrator"
    echo "Network Communication Determinism"
    echo "========================================================"
    echo "Runs per primary condition: $RUNS_PER_CONDITION"
    echo "Packets per run:            $PACKETS_PER_RUN"
    echo "Runs per netem condition:   $NETEM_RUNS (${NETEM_PACKETS} pkts)"
    echo "Results directory:          $RESULTS_DIR"
    echo ""

    check_dependencies

    # Build the list of conditions to run
    local all_primary=("${PRIMARY_NAMES[@]}")
    local all_netem=("${NETEM_NAMES[@]}")

    if [[ -n "$SINGLE_CONDITION" ]]; then
        # Validate and route to the right runner
        if [[ -n "${PRIMARY_FLOOD_MBPS[$SINGLE_CONDITION]+x}" ]]; then
            run_primary_condition "$SINGLE_CONDITION"
        elif [[ -n "${NETEM_PARAMS[$SINGLE_CONDITION]+x}" ]]; then
            run_netem_condition "$SINGLE_CONDITION"
        else
            echo "ERROR: unknown condition '$SINGLE_CONDITION'"
            echo "Primary:  ${PRIMARY_NAMES[*]}"
            echo "Netem:    ${NETEM_NAMES[*]}"
            exit 1
        fi
        return
    fi

    local total_primary=$(( ${#all_primary[@]} * RUNS_PER_CONDITION ))
    local total_netem=$(( ${#all_netem[@]} * NETEM_RUNS ))
    local total=$(( total_primary + total_netem ))
    local est_min=$(( (total_primary * 8 + total_netem * 2 +
                       (${#all_primary[@]} + ${#all_netem[@]}) * 180) / 60 ))
    echo "Total runs: $total  ($total_primary primary + $total_netem netem)"
    echo "Estimated:  ~${est_min} minutes"
    echo "QEMU restarts once per condition."
    echo ""

    for cond in "${all_primary[@]}"; do
        run_primary_condition "$cond"
    done

    for cond in "${all_netem[@]}"; do
        run_netem_condition "$cond"
    done

    echo ""
    echo "========================================================"
    echo "All conditions completed."
    echo "Results saved to: $RESULTS_DIR"
    echo "========================================================"
    echo ""
    echo "Next: python3 experiments/analyze_scenario3.py"
}

main
