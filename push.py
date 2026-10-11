#!/usr/bin/env python3
"""Push every file under <node_name>/ to /root on that node, over the
console (any path, /root included - the GNS3 files API 404s there).
Relative layout under the node's local directory is preserved under
/root (subdirectories are created with mkdir -p first).

Usage:
    ./push.py <node_name>

Example:
    ./push.py node1
"""
import sys
from pathlib import Path, PurePosixPath

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
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <node_name>", file=sys.stderr)
        raise SystemExit(1)

    node_name = sys.argv[1]

    src_dir = ROOT / node_name
    if not src_dir.is_dir():
        print(f"error: {src_dir} not found", file=sys.stderr)
        raise SystemExit(1)

    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    if not files:
        print(f"error: no files under {src_dir}", file=sys.stderr)
        raise SystemExit(1)

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

    print(f"-- connecting to {host} as {username} --", file=sys.stderr)
    base_url = host if "://" in host else f"http://{host}"
    client = Gns3Client(base_url=base_url, username=username, password=password)

    try:
        print(f"-- resolving project '{project_name}' and node '{node_name}' --", file=sys.stderr)
        project = client.find_project(project_name)
        if project is None:
            print(f"error: project '{project_name}' not found on {base_url}", file=sys.stderr)
            raise SystemExit(1)

        node = client.find_node(project["project_id"], node_name)
        if node is None:
            print(f"error: node '{node_name}' not found in project '{project_name}'", file=sys.stderr)
            raise SystemExit(1)
    except Gns3ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    made_dirs: set[str] = set()
    try:
        for path in files:
            rel = path.relative_to(src_dir)
            remote_path = f"/root/{rel.as_posix()}"
            remote_dir = str(PurePosixPath(remote_path).parent)
            if remote_dir != "/root" and remote_dir not in made_dirs:
                client.console_exec(
                    project["project_id"], node["node_id"], f"mkdir -p {remote_dir}",
                    transport="websocket",
                )
                made_dirs.add(remote_dir)

            mode = 0o755 if path.stat().st_mode & 0o100 else None
            print(f"-- pushing {rel} -> {node_name}:{remote_path} --", file=sys.stderr)
            client.push_node_file(
                project["project_id"], node["node_id"], remote_path, path.read_text(),
                mode=mode, transport="websocket",
            )
    except Gns3ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"-- pushed {len(files)} file(s) to {node_name}:/root --", file=sys.stderr)


if __name__ == "__main__":
    main()
