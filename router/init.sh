#!/bin/bash
# Runs once at container boot (see /etc/debinet-init.sh). Turns this node
# into the LAN's gateway + trusted NTP/syslog infrastructure:
#   - eth0 (WAN) -> NAT1, gives the LAN a path to the internet
#   - eth1 (LAN) -> Switch1, serves node1/node2
#   - NAT (MASQUERADE) so node1/node2 can reach the internet too
#   - chrony: authenticated NTP server for the LAN
#   - rsyslog: centralized, LAN-only log collector
#   - iptables: NTP/syslog only reachable from the LAN, never from the WAN
set -e

LAN_NET=10.10.10.0/24
LAN_IP=10.10.10.1
# eth0 (WAN, towards NAT1) gets its address and default route from GNS3's
# own NAT integration automatically -- it needs no configuration here.
UPSTREAM_DNS=192.168.122.1
NTP_KEY_ID=1
NTP_KEY=2dd12fff72050c861e52c2adb18db40d6b91e9fb

# -- network ------------------------------------------------------------
ip link set eth1 up
ip addr add "$LAN_IP/24" dev eth1 2>/dev/null || true

echo "nameserver $UPSTREAM_DNS" > /etc/resolv.conf

echo 1 > /proc/sys/net/ipv4/ip_forward
iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

# -- packages -------------------------------------------------------------
if ! command -v chronyd >/dev/null 2>&1 || ! command -v rsyslogd >/dev/null 2>&1; then
    for _ in $(seq 1 5); do
        if apt-get -o Acquire::ForceIPv4=true update -qq \
            && apt-get -o Acquire::ForceIPv4=true install -y -qq --no-install-recommends chrony rsyslog
        then
            break
        fi
        sleep 3
    done
fi

# -- chrony: authenticated NTP server for the LAN --------------------------
# A symmetric key here means a client only accepts time from a server that
# knows this key, closing off the classic off-path NTP spoofing attack
# (inject a bogus reply, skew the client's clock, then ride that skew to
# defeat TLS cert checks or to make its logs' timestamps lie).
cat > /etc/chrony/chrony.keys <<KEYS
$NTP_KEY_ID SHA1 HEX:$NTP_KEY
KEYS
chmod 640 /etc/chrony/chrony.keys

cat > /etc/chrony/chrony.conf <<CONF
pool pool.ntp.org iburst

allow $LAN_NET
local stratum 10

keyfile /etc/chrony/chrony.keys
driftfile /var/lib/chrony/chrony.drift
rtcsync
CONF

mkdir -p /var/lib/chrony
pgrep -x chronyd >/dev/null || { rm -f /run/chrony/chronyd.pid; chronyd; }

# -- rsyslog: centralized, access-controlled log collector -----------------
# Logs land in one place an attacker on node1/node2 can't reach to tamper
# with, and every entry's timestamp is trustworthy because chrony (above)
# keeps this node and its clients on the same authenticated clock -- without
# that, correlating events across hosts during an incident is guesswork.
mkdir -p /var/log/remote
cat > /etc/rsyslog.d/10-central.conf <<CONF
module(load="imudp")
module(load="imtcp")

input(type="imudp" port="514" address="$LAN_IP")
input(type="imtcp" port="514" address="$LAN_IP")

\$AllowedSender UDP, $LAN_NET
\$AllowedSender TCP, $LAN_NET

template(name="PerHostLog" type="string" string="/var/log/remote/%HOSTNAME%/syslog.log")

if \$fromhost-ip startswith '10.10.10.' and \$fromhost-ip != '$LAN_IP' then {
    action(type="omfile" dynaFile="PerHostLog")
    stop
}
CONF
pgrep -x rsyslogd >/dev/null || { rm -f /run/rsyslogd.pid; rsyslogd; }

# -- firewall: NTP/syslog answer the LAN only, the WAN gets nothing --------
for proto_port in "udp 123" "udp 514" "tcp 514"; do
    set -- $proto_port
    iptables -C INPUT -i eth1 -p "$1" --dport "$2" -j ACCEPT 2>/dev/null \
        || iptables -A INPUT -i eth1 -p "$1" --dport "$2" -j ACCEPT
    iptables -C INPUT -i eth0 -p "$1" --dport "$2" -j DROP 2>/dev/null \
        || iptables -A INPUT -i eth0 -p "$1" --dport "$2" -j DROP
done

echo "[init.sh] router ready: NAT $LAN_NET -> eth0, NTP+syslog on $LAN_IP (LAN-only)"
