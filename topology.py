#!/usr/bin/env python3
"""Idempotently builds the kjk-syslog-ntp GNS3 topology:

    NAT1 -- router --- Switch1 -+- node1
          (eth0/WAN) (eth1/LAN)  +- node2

router is the LAN gateway: it NATs node1/node2 to the internet (for package
installs) and hosts the NTP server + syslog server the rest of init.sh
configures. All three guest nodes use the debinet image.

Safe to re-run: ensure_node/ensure_link skip whatever already exists.

Usage:
    ./topology.py
"""
import sys
from pathlib import Path

import urllib3
from gns3api import Gns3ApiError, Gns3Client

ROOT = Path(__file__).resolve().parent
DEBINET_IMAGE = "ardhptr21/debinet:latest"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def main() -> None:
    creds_path = ROOT / "creds.env"
    creds = load_env(creds_path)
    host, username, password, project_name = (
        creds.get("GNS3_HOST"), creds.get("USERNAME"), creds.get("PASSWORD"), creds.get("PROJECT_NAME"),
    )
    missing = [
        k for k, v in (
            ("GNS3_HOST", host), ("USERNAME", username), ("PASSWORD", password), ("PROJECT_NAME", project_name),
        ) if not v
    ]
    if missing:
        print(f"error: {', '.join(missing)} not set in {creds_path}", file=sys.stderr)
        raise SystemExit(1)

    base_url = host if "://" in host else f"http://{host}"
    client = Gns3Client(base_url=base_url, username=username, password=password)

    try:
        print(f"-- resolving project '{project_name}' --", file=sys.stderr)
        project = client.find_project(project_name)
        if project is None:
            print(f"-- creating project '{project_name}' --", file=sys.stderr)
            project = client.create_project(project_name)
        pid = project["project_id"]
        client.open_project(pid)

        nat_template = client.find_template("NAT")
        switch_template = client.find_template("Ethernet switch")
        debinet_template = client.find_template("debinet")
        for name, template in (("NAT", nat_template), ("Ethernet switch", switch_template), ("debinet", debinet_template)):
            if template is None:
                print(f"error: template '{name}' not found on {base_url}", file=sys.stderr)
                raise SystemExit(1)

        print("-- ensuring nodes --", file=sys.stderr)
        nat = client.ensure_node(
            pid, "NAT1", nat_template["template_id"], compute_id="local", node_type="nat", x=0, y=0,
        )
        # console_type is set explicitly: the generic nodes endpoint (unlike
        # creating from a template directly) doesn't default it for docker
        # nodes, which otherwise leaves the console silently unreachable.
        router = client.ensure_node(
            pid, "router", debinet_template["template_id"], compute_id="local", node_type="docker",
            console_type="telnet", x=200, y=0, properties={"adapters": 2, "image": DEBINET_IMAGE},
        )
        switch = client.ensure_node(
            pid, "Switch1", switch_template["template_id"], compute_id="local", node_type="ethernet_switch",
            x=400, y=0,
        )
        node1 = client.ensure_node(
            pid, "node1", debinet_template["template_id"], compute_id="local", node_type="docker",
            console_type="telnet", x=600, y=-100, properties={"adapters": 1, "image": DEBINET_IMAGE},
        )
        node2 = client.ensure_node(
            pid, "node2", debinet_template["template_id"], compute_id="local", node_type="docker",
            console_type="telnet", x=600, y=100, properties={"adapters": 1, "image": DEBINET_IMAGE},
        )

        for name, node in (("router", router), ("node1", node1), ("node2", node2)):
            if node.get("console_type") != "telnet":
                client.update_node(pid, node["node_id"], console_type="telnet")
                print(f"-- fixed up {name}'s console_type (was unset) --", file=sys.stderr)

        print("-- ensuring links --", file=sys.stderr)
        # router eth0 (adapter 0) -> NAT1 (WAN uplink)
        client.ensure_link(pid, router["node_id"], 0, 0, nat["node_id"], 0, 0)
        # router eth1 (adapter 1) -> Switch1 port 0 (LAN)
        client.ensure_link(pid, router["node_id"], 1, 0, switch["node_id"], 0, 0)
        # node1/node2 eth0 -> Switch1 port 1/2
        client.ensure_link(pid, node1["node_id"], 0, 0, switch["node_id"], 0, 1)
        client.ensure_link(pid, node2["node_id"], 0, 0, switch["node_id"], 0, 2)
    except Gns3ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print("-- topology ready: NAT1 - router - Switch1 - {node1, node2} --", file=sys.stderr)


if __name__ == "__main__":
    main()
