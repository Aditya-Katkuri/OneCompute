"""``python -m orchestrator``: run the OneCompute control plane on a LAN PC.

Binds uvicorn to a configurable host/port (default ``0.0.0.0:8080`` so worker
machines on the same network can reach it) and persists the fleet/queue/ledger to
a file-backed SQLite DB (default ``./reeve-orchestrator.db``) so state survives a
restart. On startup it prints, for every detected LAN IPv4, the dashboard URL and
the exact worker command, plus a reachability hint and a trust caveat.

Config precedence (highest first): CLI flag > environment variable > default.
  --host / REEVE_HOST   (default 0.0.0.0)
  --port / REEVE_PORT   (default 8080)
  --db   / REEVE_DB     (default ./reeve-orchestrator.db)

Bad/missing env values degrade gracefully with a clear warning; the process never
throws on startup. Ctrl-C shuts the server down cleanly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import uvicorn

from orchestrator.app import create_app
from orchestrator.netinfo import lan_ipv4_addresses, primary_lan_ipv4

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_DB = "reeve-orchestrator.db"


def _coerce_port(raw: str, fallback: int, source: str) -> tuple[int, str | None]:
    """Parse a port from ``raw``; on bad/empty input return ``fallback`` + a warning.

    Returns ``(port, warning_or_None)`` and never raises.
    """
    if not raw:
        return fallback, None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return fallback, f"{source}={raw!r} is not an integer; using {fallback}"
    if not (1 <= port <= 65535):
        return fallback, f"{source}={raw} is out of range 1-65535; using {fallback}"
    return port, None


def _resolve_defaults() -> tuple[str, int, str, str | None]:
    """Resolve host/port/db defaults from env vars, with a port warning if any."""
    host = (os.environ.get("REEVE_HOST") or "").strip() or DEFAULT_HOST
    db = (os.environ.get("REEVE_DB") or "").strip() or DEFAULT_DB
    port, warning = _coerce_port(
        (os.environ.get("REEVE_PORT") or "").strip(), DEFAULT_PORT, "REEVE_PORT"
    )
    return host, port, db, warning


def _prepare_db_path(raw: str) -> str:
    """Return an absolute DB path, creating its parent dir if missing. Never raises.

    SQLite will not create missing parent directories, so we do it here to keep
    startup from throwing on a fresh path like ``C:\\onecompute\\fleet.db``.
    """
    db_path = os.path.abspath(raw)
    parent = os.path.dirname(db_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            print(f"warning: could not create DB directory {parent!r}: {exc}", file=sys.stderr)
    return db_path


def _banner_lines(host: str, port: int, db_path: str, scheme: str = "http") -> list[str]:
    """Build the startup banner showing dashboard URL(s) + the worker command."""
    addrs = lan_ipv4_addresses()
    line = "=" * 60
    out = [
        line,
        "  OneCompute Orchestrator",
        f"  Bind:  {host}:{port}  ({scheme.upper()})",
        f"  DB:    {db_path}  (persistent)",
        "",
    ]
    if addrs:
        for ip in addrs:
            out.append(f"  Dashboard:  {scheme}://{ip}:{port}/")
            out.append(f"  Worker:     uv run python -m worker --url {scheme}://{ip}:{port}")
        # Prefer the routed/primary NIC for the reachability hint over the
        # (sorted) first address, which may be a virtual/VPN interface.
        primary = primary_lan_ipv4() or addrs[0]
        out += [
            "",
            "  Reachability: from a worker PC, first confirm",
            f"                curl {scheme}://{primary}:{port}/state  returns JSON",
        ]
    else:
        # No usable LAN IP detected. Fall back to local-only guidance.
        out += [
            f"  Dashboard:  {scheme}://127.0.0.1:{port}/",
            f"  Worker:     uv run python -m worker --url {scheme}://127.0.0.1:{port}",
            "",
            "  No LAN IPv4 detected; showing loopback. If this host is multi-homed",
            f"  or on a VPN, point workers at the reachable interface IP on port {port}.",
        ]
    out += [
        "",
        "  Trust: 0.0.0.0 exposes the control plane to the whole LAN. Fine on a",
        "         trusted/isolated switch for the PoC; allow-listing is roadmap.",
        line,
    ]
    return out


def _serve(host: str, port: int, db_path: str, log_level: str,
           tls_cert: str | None = None, tls_key: str | None = None,
           require_approval: bool = False) -> None:
    """Start uvicorn against a persistent file-backed app. Serves HTTPS when a TLS cert+key are
    given (the doctrine's 'plain HTTPS' transport for a cloud/multi-site pilot). When
    require_approval is set, joining workers are gated behind a dashboard device-code approval.
    Blocks until shutdown."""
    app = create_app(db_path, require_approval=require_approval)
    config = uvicorn.Config(
        app, host=host, port=port, log_level=log_level,
        ssl_certfile=tls_cert, ssl_keyfile=tls_key,
    )
    server = uvicorn.Server(config)
    server.run()  # installs its own SIGINT handler for clean Ctrl-C shutdown


def main(argv: list[str] | None = None) -> int:
    env_host, env_port, env_db, port_warning = _resolve_defaults()

    parser = argparse.ArgumentParser(
        prog="python -m orchestrator",
        description="Run the OneCompute orchestrator on a LAN PC.",
    )
    parser.add_argument("--host", default=None, help=f"Bind host (default {env_host})")
    parser.add_argument("--port", type=int, default=None, help=f"Bind port (default {env_port})")
    parser.add_argument("--db", default=None, help=f"SQLite file path (default {env_db})")
    parser.add_argument("--log-level", default="info", help="uvicorn log level (default info)")
    parser.add_argument("--tls-cert", default=None, help="TLS cert file (serve HTTPS; pair with --tls-key)")
    parser.add_argument("--tls-key", default=None, help="TLS key file (serve HTTPS; pair with --tls-cert)")
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="Gate joining workers behind a dashboard device-code approval (a worker shows a code "
             "and is PENDING until an admin clicks Approve in the dashboard).",
    )
    args = parser.parse_args(argv)

    host = args.host if args.host is not None else env_host
    if args.port is not None:
        # Route CLI --port through the same validation as the env var so an
        # out-of-range value degrades gracefully instead of crashing in bind().
        port, cli_warning = _coerce_port(str(args.port), env_port, "--port")
        if cli_warning:
            print(f"warning: {cli_warning}", file=sys.stderr)
    else:
        port = env_port
        if port_warning:
            print(f"warning: {port_warning}", file=sys.stderr)
    db_path = _prepare_db_path(args.db if args.db is not None else env_db)
    tls = bool(args.tls_cert and args.tls_key)
    if bool(args.tls_cert) != bool(args.tls_key):
        print("warning: --tls-cert and --tls-key must be given together; serving HTTP", file=sys.stderr)
    scheme = "https" if tls else "http"

    for banner_line in _banner_lines(host, port, db_path, scheme):
        print(banner_line)
    if args.require_approval:
        print("  Credential gate: ON. Workers join PENDING and need dashboard approval (device code).")
    sys.stdout.flush()

    try:
        _serve(
            host, port, db_path, args.log_level,
            args.tls_cert if tls else None, args.tls_key if tls else None,
            require_approval=args.require_approval,
        )
    except KeyboardInterrupt:
        print("\nshutting down (Ctrl-C)")
    except (OSError, sqlite3.Error) as exc:
        print(
            f"error: could not start orchestrator on {host}:{port} - {exc}. "
            "Is the port already in use or the DB path unwritable? Try --port / --db.",
            file=sys.stderr,
        )
        return 1
    except SystemExit as exc:
        # uvicorn catches bind failures internally and calls sys.exit(1); surface
        # actionable guidance instead of a bare exit code.
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            print(
                f"error: could not bind {host}:{port}. Is the port already in use? "
                "Try --port <n>.",
                file=sys.stderr,
            )
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
