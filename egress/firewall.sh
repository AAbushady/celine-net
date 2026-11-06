#!/bin/sh
# Egress firewall for a box that runs whatever the agent types or opens.
#
# This container owns the network namespace. The box joins it (network_mode:
# service:<this>) and has no NET_ADMIN, so nothing in the box can change these rules,
# root included. If this container stops, the box loses its network: it fails closed.
#
# Out: loopback (Docker's DNS), the box's own network except its gateway (that is the
# app), and public addresses. Dropped: the gateway and every private, link-local, CGNAT
# and reserved range, which covers the LAN and the Docker host (host.docker.internal).
# IPv6 is dropped whole. Any failure exits here, the service never turns healthy and the
# box never starts.
set -eu

# an `internal: true` network has no default route, so there is no gateway to drop
gateway=$(ip -4 route show default | awk '{print $3; exit}')
subnet=$(ip -4 route show scope link | awk '{print $1; exit}')
if [ -z "$subnet" ]; then
    echo "firewall: no route to read" >&2
    exit 1
fi

iptables -P OUTPUT DROP
iptables -P FORWARD DROP
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# before the allowed list, so a wide entry there cannot open the Docker host
if [ -n "$gateway" ]; then
    iptables -A OUTPUT -d "$gateway" -j DROP
fi
# EGRESS_ALLOW (.env): what the owner chose to keep reachable, each entry ADDRESS:PORT (TCP
# to that port) or ADDRESS (the whole machine). A value iptables refuses stops the script,
# so the box does not start.
for entry in $(echo "${EGRESS_ALLOW:-}" | tr ',' ' '); do
    case $entry in
        *:*) iptables -A OUTPUT -p tcp -d "${entry%:*}" --dport "${entry##*:}" -j ACCEPT ;;
        *)   iptables -A OUTPUT -d "$entry" -j ACCEPT ;;
    esac
done
iptables -A OUTPUT -d "$subnet" -j ACCEPT
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
           192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 \
           203.0.113.0/24 224.0.0.0/4 240.0.0.0/4; do
    iptables -A OUTPUT -d "$net" -j DROP
done
iptables -A OUTPUT -j ACCEPT

ip6tables -P INPUT DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A OUTPUT -o lo -j ACCEPT

echo "firewall: up, gateway ${gateway:-none} dropped, $subnet allowed"
touch /run/firewall-up
exec sleep 2147483647
