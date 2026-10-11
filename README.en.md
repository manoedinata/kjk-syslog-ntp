# kjk-syslog-ntp

A GNS3 lab demonstrating how **NTP** and **Syslog** function as network
security controls, not just operational conveniences: accurate,
authenticated time underpins trustworthy logs, and centralized,
access-controlled logging gives you a record an attacker on an endpoint
can't erase.

*(Versi Indonesia: [README.md](README.md))*

## Topology

```
                      WAN (auto-configured by GNS3)
   NAT1 ─────────────────── eth0 ┐
                                 │  router
                   10.10.10.1 ── eth1 ┐
                                      │
                                 Switch1
                                 ┌─────┴─────┐
                          10.10.10.11   10.10.10.12
                            node1          node2
```

All three guest nodes run the `debinet` Docker image (Debian trixie).
`router` is the LAN's gateway, NAT, NTP server, and syslog server all in
one — the trusted infrastructure node. `node1`/`node2` are its clients.

- `router` eth0 → `NAT1`: WAN uplink, IP/route handed out automatically
  by GNS3's NAT integration (nothing to configure).
- `router` eth1 → `Switch1` → `node1`/`node2`: the internal LAN
  (`10.10.10.0/24`).
- `router` does `iptables` MASQUERADE so `node1`/`node2` can reach the
  internet (needed for their own `apt-get install`) through it.

## How NTP secures the network

NTP (Network Time Protocol) synchronizes clocks across machines. On its
own that's just convenience — but a lot of security depends on it:

- **Log correlation.** An incident almost always spans multiple hosts.
  If their clocks disagree, you can't reliably tell which event on
  `node1` happened before or after which event on `node2` — the
  timeline an investigation depends on falls apart.
- **Certificate and token validity.** TLS certificate expiry, Kerberos
  tickets, TOTP codes, signed JWTs — all of these are time-windowed. A
  client with a wrong clock can be tricked into accepting an expired
  cert as valid, or rejecting a legitimate one.
- **Spoofing risk.** NTP itself can be attacked: an on-path or
  off-path attacker who can inject NTP responses can skew a victim's
  clock, then use that skew to defeat the time-based checks above, or
  to make a compromised host's own log timestamps lie about when
  something happened.

This lab's defense against that last point is **symmetric-key NTP
authentication**: `router` and both clients share a SHA1 key
(`/etc/chrony/chrony.keys`). A client only accepts a time update from a
server that can prove it holds the same key — an attacker on the LAN
without the key gets NAK'd and ignored, no matter how convincing the
spoofed packet looks.

```
# router — chrony.conf (excerpt)
allow 10.10.10.0/24       # serve authenticated time to the LAN only
local stratum 10          # keep serving even if the upstream pool is briefly unreachable
keyfile /etc/chrony/chrony.keys

# node1/node2 — chrony.conf
server 10.10.10.1 iburst key 1   # only trust router, and only if it proves the key
```

Check it live:

```
./console.py node1 "chronyc -a authdata"   # KeyID 1, NAK=0 -> authenticated, no forged replies accepted
./console.py router "chronyc -a sources"   # router's own upstream sync
```

## How Syslog secures the network

Syslog is the standard protocol for a machine to emit log messages.
Centralizing those messages onto a separate, access-controlled server
is itself a security control:

- **Tamper resistance.** If `node1` gets compromised, the attacker can
  delete or rewrite its local logs to cover their tracks — but the
  copy already shipped to `router` is out of their reach. The record
  of what happened survives.
- **Single pane of glass.** Correlating an attack across several hosts
  is far easier when every host's events land in one place, instead of
  SSH-ing into each one to go looking.
- **Access control at the collector.** `router` only accepts syslog
  traffic from the internal LAN (`10.10.10.0/24`), on both UDP and TCP
  514 — so a host on the WAN side can't inject forged log entries to
  frame another machine or bury real ones in noise.
- **Trustworthy timestamps.** This is where NTP and syslog meet: a
  centralized log is only useful for correlation if every entry's
  timestamp is accurate, which is exactly what the authenticated NTP
  setup above guarantees.

```
# node1/node2 — forward everything to router over TCP
*.* action(type="omfwd" target="10.10.10.1" port="514" protocol="tcp" ...)

# router — accept only from the LAN, file per sending host
$AllowedSender TCP, 10.10.10.0/24
template(name="PerHostLog" type="string" string="/var/log/remote/%HOSTNAME%/syslog.log")
```

Check it live:

```
./console.py node1 "logger -t demo hello-from-node1"
./console.py router "cat /var/log/remote/node1/syslog.log"
```

`router`'s `iptables` rules reinforce the same boundary at the packet
level: NTP (123/udp) and syslog (514/udp,tcp) are `ACCEPT`ed from
`eth1` (the LAN) and explicitly `DROP`ped from `eth0` (the WAN) — so
even if someone found their way onto the WAN side, these services
aren't reachable from there at all.

## Workspace layout

```
creds.env           # GNS3 host/credentials + PROJECT_NAME (gitignored)
creds.env.example   # template for the above
topology.py         # idempotently builds the GNS3 topology (nodes + links)
console.py          # ./console.py <node_name> [command...] -- open a shell, or run one command
push.py             # ./push.py <node_name> -- push <node_name>/* to /root on that node
router/init.sh       # router's boot-time provisioning script
node1/init.sh        # node1's boot-time provisioning script
node2/init.sh        # node2's boot-time provisioning script
```

`init.sh` is the per-node entry point: the `debinet` image's own
`/etc/debinet-init.sh` runs `/root/init.sh` automatically on container
boot if it's present and executable, before dropping to an interactive
shell. That's where all of the configuration above (addressing, NAT,
chrony, rsyslog, firewall) actually gets applied — `push.py` just gets
the file there.

## Usage

```
cp creds.env.example creds.env   # fill in GNS3_HOST / USERNAME / PASSWORD / PROJECT_NAME

./topology.py                    # create/update the GNS3 topology (safe to re-run)

./push.py router                 # push router/init.sh -> router:/root/init.sh
./push.py node1
./push.py node2

# then stop/start (or reload) each node from the GNS3 GUI so init.sh runs
```

Restarting a node re-runs its `init.sh`. Package installs are skipped
on a node that already has `chronyd`/`rsyslogd` (so restarts are fast),
and `node1`/`node2` wait for `router` to come up before their own first
`apt-get`, since all three may boot in parallel.

To poke around afterwards:

```
./console.py node1                      # interactive shell on node1
./console.py router "iptables -L -v -n" # one-shot command, no shell
```
