#!/bin/sh

# Mark net_sender as executable.
# Binaries lose their execute permission when copied into the root tarball.
chmod +x /net_sender

# Suppress ICMP Port Unreachable responses for the background flood port.
# Without this, every flood packet addressed to port 5001 (where vm-hi has
# no listener) generates an ICMP reply, adding unnecessary load on vm-hi's
# network stack during the experiment.
# iptables may not be available on the safety kernel — failure is non-fatal.
iptables -I INPUT -p udp --dport 5001 -j DROP 2>/dev/null || true
