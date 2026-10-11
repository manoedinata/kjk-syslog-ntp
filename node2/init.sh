#!/bin/bash
# Runs once at container boot (see /etc/debinet-init.sh). Joins the LAN
# behind router and becomes one of its secured clients:
#   - chrony: NTP client, authenticated against router's key
#   - rsyslog: forwards every local log to router's central collector
set -e

LAN_IP=10.10.10.12
ROUTER_IP=10.10.10.1
# router only NATs/forwards -- it runs no DNS service of its own, so
# clients resolve via the same upstream resolver router itself uses
# (reachable transparently through router's NAT, same as any other traffic)
UPSTREAM_DNS=192.168.122.1
NTP_KEY_ID=1
NTP_KEY=2dd12fff72050c861e52c2adb18db40d6b91e9fb

# -- network ------------------------------------------------------------
ip link set eth0 up
ip addr add "$LAN_IP/24" dev eth0 2>/dev/null || true
ip route replace default via "$ROUTER_IP"
echo "nameserver $UPSTREAM_DNS" > /etc/resolv.conf

# -- packages -------------------------------------------------------------
# router boots in parallel and needs a moment to bring up NAT -- wait for
# it rather than racing it on the very first apt-get.
for _ in $(seq 1 15); do
    ping -c1 -W1 "$ROUTER_IP" >/dev/null 2>&1 && break || true
    sleep 1
done

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

# -- chrony: authenticated NTP client ---------------------------------------
# Trusts router's clock only because it can prove it holds the same key --
# an attacker on the LAN spoofing NTP replies gets ignored (NAK'd), so this
# node's clock, and therefore its own logs' timestamps, can't be quietly
# skewed by someone other than router.
cat > /etc/chrony/chrony.keys <<KEYS
$NTP_KEY_ID SHA1 HEX:$NTP_KEY
KEYS
chmod 640 /etc/chrony/chrony.keys

cat > /etc/chrony/chrony.conf <<CONF
server $ROUTER_IP iburst key $NTP_KEY_ID
keyfile /etc/chrony/chrony.keys
driftfile /var/lib/chrony/chrony.drift
rtcsync
CONF

mkdir -p /var/lib/chrony
pgrep -x chronyd >/dev/null || { rm -f /run/chrony/chronyd.pid; chronyd; }

# -- rsyslog: forward everything to the central collector -------------------
# If this node is compromised, the attacker can shred its local logs, but
# the copy already shipped to router survives for the real investigation.
cat > /etc/rsyslog.d/90-forward.conf <<CONF
*.* action(type="omfwd" target="$ROUTER_IP" port="514" protocol="tcp"
    action.resumeRetryCount="-1"
    queue.type="linkedList" queue.saveOnShutdown="on")
CONF
pgrep -x rsyslogd >/dev/null || { rm -f /run/rsyslogd.pid; rsyslogd; }

echo "[init.sh] node2 ready: NTP via $ROUTER_IP (authenticated), syslog forwarded to $ROUTER_IP:514/tcp"
