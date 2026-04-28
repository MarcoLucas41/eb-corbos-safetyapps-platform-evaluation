#!/bin/bash
#
# Scenario 3 TCP Experiment Orchestrator — Network Communication Determinism
#
# TCP variant of run_scenario3.sh.  Identical in structure and conditions
# except it invokes net_receiver_tcp (TCP client) instead of net_receiver
# (UDP) and writes results to results_scenario3_tcp/.
#
# net_sender_tcp on vm-hi acts as the TCP server; net_receiver_tcp connects
# to it, collects N messages over a persistent TCP connection, then closes
# the connection to signal end-of-run.  The server re-accepts for the next run
# automatically, so no QEMU reboot is needed between runs of the same condition.
#
# Background interference uses net_flood (UDP) exactly as in the UDP variant —
# the flood saturates the shared virtual NIC regardless of the measurement
# protocol.
#
# Results are compatible with analyze_scenario3.py (identical output format).
#
# Usage:
#   ./experiments/run_scenario3_tcp.sh                    # all conditions
#   ./experiments/run_scenario3_tcp.sh -n 5               # 5 runs per condition
#   ./experiments/run_scenario3_tcp.sh -m 1000            # 1000 packets per run
#   ./experiments/run_scenario3_tcp.sh -c C0_baseline     # single condition
#   ./experiments/run_scenario3_tcp.sh -c D1_delay_1ms    # single netem condition
#

set -o pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../images/arm64/qemu/ebclfsa" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results_tcp/$(date +%Y-%m-%d_%H:%M:%S)"

RUNS_PER_CONDITION=10
PACKETS_PER_RUN=5000
SINGLE_CONDITION=""

SSH_PORT=2222
SSH_USER="root"
SSH_HOST="localhost"
SSH_PASS="linux"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

BOOT_MARKER="Ubuntu 22.04 LTS ebclfsa-li hvc0"
BOOT_TIMEOUT=180
SSH_TIMEOUT=120

VNET_LI_IP="10.0.1.2"
VNET_NETMASK="24"

# ─── Primary Condition Definitions ───────────────────────────────────────────

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
NETEM_LABELS[D1_delay_1ms]="netem delay 1ms"
NETEM_LABELS[D2_delay_5ms_jitter]="netem delay 5ms 1ms jitter"
NETEM_LABELS[L1_loss_0p5]="netem loss 0.5%"
NETEM_LABELS[L2_loss_2p0]="netem loss 2%"

NETEM_RUNS=5
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
            echo "Primary:  ${PRIMARY_NAMES[*]}"
            echo "Netem:    ${NETEM_NAMES[*]}"
            echo ""
            echo "Results: $RESULTS_DIR"
            echo "Analyze: python3 experiments/analyze_scenario3.py -r $RESULTS_DIR"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ─── SSH Helper ───────────────────────────────────────────────────────────────

run_ssh() {
    sshpass -p "$SSH_PASS" ssh $SSH_OPTS -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" "$@"
}

# ─── QEMU Lifecycle ───────────────────────────────────────────────────────────

QEMU_PID=""

start_qemu() {
    local log_file="$1"
    echo "Starting QEMU (TCP image)..."
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
        sleep 10; elapsed=$((elapsed + 10))
        if ! pgrep -f qemu-system-aarch64 &>/dev/null; then
            echo "ERROR: QEMU died. Check $log_file."
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
    echo "ERROR: SSH timeout."
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

# ─── Interface configuration ──────────────────────────────────────────────────

configure_vnet_iface() {
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

    [[ -z "$iface" ]] && { echo "WARNING: falling back to eth1"; iface="eth1"; }

    echo "Configuring $iface: ${VNET_LI_IP}/${VNET_NETMASK}"
    run_ssh "
        ip link set '$iface' up
        ip addr flush dev '$iface' 2>/dev/null || true
        ip addr add '${VNET_LI_IP}/${VNET_NETMASK}' dev '$iface'
    " 2>/dev/null

    if run_ssh "ping -c 1 -W 2 10.0.1.1" &>/dev/null; then
        echo "Reachability check: vm-hi (10.0.1.1) OK — net_sender_tcp should be listening."
    else
        echo "WARNING: vm-hi not reachable yet — waiting an extra 3s for net_sender_tcp."
        sleep 3
    fi

    echo "$iface"
}

apply_netem()  { run_ssh "tc qdisc add dev '$1' root netem $2" 2>/dev/null || echo "WARNING: tc netem failed"; }
remove_netem() { run_ssh "tc qdisc del dev '$1' root 2>/dev/null || true"; }

# ─── Run a Primary Condition ──────────────────────────────────────────────────

run_primary_condition() {
    local name="$1"
    local mbps="${PRIMARY_FLOOD_MBPS[$name]}"
    local label="${PRIMARY_LABELS[$name]}"
    local cond_dir="$RESULTS_DIR/$name"
    local qemu_log="$cond_dir/qemu_serial.log"
    mkdir -p "$cond_dir"

    local flood_arg=""
    [[ "$mbps" -gt 0 ]] && flood_arg="-B $mbps"
    # net_receiver_tcp shares the same CLI as net_receiver
    local cmd="net_receiver_tcp -n $PACKETS_PER_RUN $flood_arg"

    echo ""
    echo "============================================"
    echo "TCP Condition: $name  ($label)"
    echo "Command:       $cmd"
    echo "Runs:          $RUNS_PER_CONDITION"
    echo "============================================"

    start_qemu "$qemu_log"
    wait_for_boot "$qemu_log" || { stop_qemu; return 1; }
    wait_for_ssh             || { stop_qemu; return 1; }

    local vnet_iface
    vnet_iface=$(configure_vnet_iface)
    # net_sender_tcp is already listening; give it 1s to settle
    sleep 1

    for run in $(seq 1 "$RUNS_PER_CONDITION"); do
        local run_file="$cond_dir/run_$(printf '%02d' "$run").txt"
        echo -n "[$name] Run $run/$RUNS_PER_CONDITION ... "

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        if [[ $exit_code -gt 1 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            local seq_errs mean_owl p99_owl
            seq_errs=$(grep -oP 'Packets lost:\s+\K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            mean_owl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            p99_owl=$(grep -oP 'P99:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            local flag=""
            [[ "$exit_code" -eq 1 ]] && flag=" [SEQ ERRORS]"
            echo "OK (seq_errs: $seq_errs, OWL mean: ${mean_owl} us, P99: ${p99_owl} us)${flag}"
        fi

        # TCP: net_sender_tcp re-accepts automatically; brief pause for cleanup
        sleep 1
    done

    stop_qemu
}

# ─── Run a Netem Condition ────────────────────────────────────────────────────

run_netem_condition() {
    local name="$1"
    local netem_params="${NETEM_PARAMS[$name]}"
    local label="${NETEM_LABELS[$name]}"
    local cond_dir="$RESULTS_DIR/$name"
    local qemu_log="$cond_dir/qemu_serial.log"
    mkdir -p "$cond_dir"

    local cmd="net_receiver_tcp -n $NETEM_PACKETS"

    echo ""
    echo "============================================"
    echo "TCP Netem condition: $name  ($label)"
    echo "Command:             $cmd"
    echo "Runs:                $NETEM_RUNS"
    echo "============================================"

    start_qemu "$qemu_log"
    wait_for_boot "$qemu_log" || { stop_qemu; return 1; }
    wait_for_ssh             || { stop_qemu; return 1; }

    local vnet_iface
    vnet_iface=$(configure_vnet_iface)
    sleep 1

    apply_netem "$vnet_iface" "$netem_params"

    for run in $(seq 1 "$NETEM_RUNS"); do
        local run_file="$cond_dir/run_$(printf '%02d' "$run").txt"
        echo -n "[$name] Run $run/$NETEM_RUNS ... "

        run_ssh "$cmd" > "$run_file" 2>&1
        local exit_code=$?

        if [[ $exit_code -gt 1 ]]; then
            echo "FAILED (exit code: $exit_code)"
        else
            local seq_errs mean_owl
            seq_errs=$(grep -oP 'Packets lost:\s+\K[0-9]+' "$run_file" 2>/dev/null || echo "?")
            mean_owl=$(grep -oP 'Mean:\s+\K[0-9.]+' "$run_file" | head -1 || echo "?")
            local flag=""
            [[ "$exit_code" -eq 1 ]] && flag=" [SEQ ERRORS]"
            echo "OK (seq_errs: $seq_errs, OWL mean: ${mean_owl} us)${flag}"
        fi

        sleep 1
    done

    remove_netem "$vnet_iface"
    stop_qemu
}

# ─── Preflight ────────────────────────────────────────────────────────────────

check_dependencies() {
    local missing=0
    for cmd in sshpass task qemu-system-aarch64; do
        command -v "$cmd" &>/dev/null || { echo "ERROR: '$cmd' not found."; missing=1; }
    done
    [[ $missing -eq 1 ]] && { echo "Install missing deps and retry."; exit 1; }
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    echo "========================================================"
    echo "Scenario 3 TCP Experiment Orchestrator"
    echo "Network Communication Determinism — TCP transport"
    echo "========================================================"
    echo "Transport:                  TCP (net_sender_tcp / net_receiver_tcp)"
    echo "Background interference:    UDP flood (net_flood)"
    echo "Runs per primary condition: $RUNS_PER_CONDITION"
    echo "Packets per run:            $PACKETS_PER_RUN"
    echo "Runs per netem condition:   $NETEM_RUNS (${NETEM_PACKETS} pkts)"
    echo "Results directory:          $RESULTS_DIR"
    echo ""
    echo "Analyze results with:"
    echo "  python3 experiments/analyze_scenario3.py -r $RESULTS_DIR"
    echo ""

    check_dependencies

    if [[ -n "$SINGLE_CONDITION" ]]; then
        if [[ -n "${PRIMARY_FLOOD_MBPS[$SINGLE_CONDITION]+x}" ]]; then
            run_primary_condition "$SINGLE_CONDITION"
        elif [[ -n "${NETEM_PARAMS[$SINGLE_CONDITION]+x}" ]]; then
            run_netem_condition "$SINGLE_CONDITION"
        else
            echo "ERROR: unknown condition '$SINGLE_CONDITION'"
            echo "Primary: ${PRIMARY_NAMES[*]}"
            echo "Netem:   ${NETEM_NAMES[*]}"
            exit 1
        fi
        return
    fi

    local total_primary=$(( ${#PRIMARY_NAMES[@]} * RUNS_PER_CONDITION ))
    local total_netem=$(( ${#NETEM_NAMES[@]} * NETEM_RUNS ))
    echo "Total runs: $(( total_primary + total_netem ))  ($total_primary primary + $total_netem netem)"
    echo "QEMU restarts once per condition."
    echo ""

    for cond in "${PRIMARY_NAMES[@]}"; do run_primary_condition "$cond"; done
    for cond in "${NETEM_NAMES[@]}";   do run_netem_condition   "$cond"; done

    echo ""
    echo "========================================================"
    echo "All TCP conditions completed."
    echo "Results: $RESULTS_DIR"
    echo "========================================================"
    echo ""
    echo "Compare UDP vs TCP:"
    echo "  python3 experiments/analyze_scenario3.py -r experiments/results_scenario3"
    echo "  python3 experiments/analyze_scenario3.py -r experiments/results_scenario3_tcp"
}

main
