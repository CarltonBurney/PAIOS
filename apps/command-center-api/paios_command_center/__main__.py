"""Command line entry point: ``python -m paios_command_center``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import RegistryError, load_projects
from .server import make_server
from .service import StatusService
from .store import Store

APP_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paios-command-center")
    parser.add_argument(
        "--projects",
        type=Path,
        default=APP_ROOT / "projects.json",
        help="project registry file (default: projects.json beside the app)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=APP_ROOT / "data" / "state.json",
        help="where manual edits and snapshots are stored",
    )
    parser.add_argument(
        "--static",
        type=Path,
        default=APP_ROOT / "wwwroot",
        help="directory holding the dashboard",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8000, help="bind port")
    parser.add_argument(
        "--poll-on-start",
        action="store_true",
        help="poll every source once before serving",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        projects = load_projects(args.projects)
    except RegistryError as exc:
        # Starting with no projects would render an empty dashboard that looks like
        # a working one, so this is fatal rather than a warning.
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: copy projects.example.json to projects.json and edit it",
            file=sys.stderr,
        )
        return 2

    service = StatusService(projects, Store(args.state))

    if args.poll_on_start:
        result = service.poll(reason="startup")
        for error in result["errors"]:
            print(f"warning: {error}", file=sys.stderr)

    httpd = make_server(service, args.static, host=args.host, port=args.port)
    print(f"Command Center on http://{args.host}:{args.port} — {len(projects)} projects")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
