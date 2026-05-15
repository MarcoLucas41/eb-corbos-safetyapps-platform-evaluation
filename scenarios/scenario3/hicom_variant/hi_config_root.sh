#!/bin/sh
# Make both HI binaries executable.
# hi_net_ping is the init process (referenced by init= in the hi VM cmdline).
# hi_net_echo is exec'd at runtime by hi_net_ping.
chmod +x /hi_net_ping /hi_net_echo

# Suppress ICMP Port Unreachable responses for the background flood port.
iptables -I INPUT -p udp --dport 5001 -j DROP 2>/dev/null || true
