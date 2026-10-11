#!/usr/bin/env python3
"""Open a live, raw console session on a GNS3 node over the websocket
transport (same byte stream xterm.js/websocat would show - real shell
prompt, ANSI colors, Ctrl-C reaches the remote shell). With an extra
command argument, runs that one command instead (no shell opened) and
prints its output.

Resolves the project by PROJECT_NAME and connects to <node_name> within
it, using the credentials/project in creds.env.

Usage:
    ./console.py <node_name> [command...]

Examples:
    ./console.py node1
    ./console.py node1 whoami
    ./console.py node1 ls -la /root
"""
import sys
from pathlib import Path

import urllib3
from gns3api import Gns3ApiError, Gns3Client

ROOT = Path(__file__).resolve().parent

# Gns3Client defaults to verify=False (GNS3 lab controllers commonly run
# self-signed certs) -- that's a deliberate choice here, not an oversight,
# so silence urllib3's warning about it instead of leaving it to spam stderr
# on every request.
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
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <node_name> [command...]", file=sys.stderr)
        raise SystemExit(1)

    node_name = sys.argv[1]
    command = " ".join(sys.argv[2:])

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

    if not command:
        print(f"-- connecting to {host} as {username} --", file=sys.stderr)
    base_url = host if "://" in host else f"http://{host}"
    client = Gns3Client(base_url=base_url, username=username, password=password)

    try:
        if not command:
            print(f"-- resolving project '{project_name}' and node '{node_name}' --", file=sys.stderr)
        project = client.find_project(project_name)
        if project is None:
            print(f"error: project '{project_name}' not found on {base_url}", file=sys.stderr)
            raise SystemExit(1)

        if not command:
            print(f"-- opening console to node '{node_name}' in project '{project_name}' --", file=sys.stderr)
        node = client.find_node(project["project_id"], node_name)
        if node is None:
            print(f"error: node '{node_name}' not found in project '{project_name}'", file=sys.stderr)
            raise SystemExit(1)
    except Gns3ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        if command:
            output = client.console_exec(
                project["project_id"], node["node_id"], command, transport="websocket",
            )
            print(output, end="" if output.endswith("\n") else "\n")
        else:
            print(f"-- console: {project_name}/{node_name} (websocket) -- Ctrl-] to disconnect --",
                  file=sys.stderr)
            client.open_console(project["project_id"], node["node_id"])
    except Gns3ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
