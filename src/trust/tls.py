"""TLS/mTLS helpers shared by the worker (client) and orchestrator (server).

Kept in one place so the client side (httpx ``verify`` / ``cert``) and the server side
(uvicorn ``ssl_*`` kwargs, or an equivalent ``ssl.SSLContext``) express the *same* policy:
pin a private CA, and optionally require **mutual TLS** where the worker presents a client
certificate the orchestrator verifies. Everything is opt-in: with no cert paths the worker
uses the system trust store and the orchestrator serves plain HTTP, preserving the
local-demo behavior. Missing files fail fast with a clear message rather than a confusing
handshake error later.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import httpx


def _require_file(path: str, what: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{what} not found: {path}")
    return str(p)


def client_ssl_params(
    ca_cert: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
) -> dict[str, Any]:
    """Return ``httpx.Client`` kwargs (``verify``) for the worker.

    - No TLS material: ``verify=True`` (the system trust store), so the local demo is unchanged.
    - ``ca_cert``: pin the orchestrator to this CA; returns an ``ssl.SSLContext``.
    - ``client_cert`` / ``client_key``: load a client certificate into that context for mutual
      TLS. Both must be given together.
    """
    if (client_cert or client_key) and not (client_cert and client_key):
        raise ValueError("mutual TLS needs BOTH --client-cert and --client-key")
    if not ca_cert and not client_cert:
        return {"verify": True}
    if ca_cert:
        ctx = ssl.create_default_context(cafile=_require_file(ca_cert, "TLS CA cert (--tls-ca)"))
    else:
        ctx = ssl.create_default_context()
    if client_cert:
        ctx.load_cert_chain(
            _require_file(client_cert, "client cert (--client-cert)"),
            _require_file(client_key, "client key (--client-key)"),
        )
    return {"verify": ctx}


def build_client(
    base_url: str,
    *,
    ca_cert: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
    timeout: float = 10.0,
) -> httpx.Client:
    """Build an ``httpx.Client`` wired for (optionally mutual) TLS.

    With no cert arguments this is equivalent to the previous plain client
    (system trust store, no client cert), so the local demo is unchanged.
    """
    params = client_ssl_params(ca_cert, client_cert, client_key)
    return httpx.Client(base_url=base_url, timeout=timeout, **params)


def server_ssl_kwargs(
    cert: str,
    key: str,
    client_ca: str | None = None,
) -> dict[str, Any]:
    """Return uvicorn ``ssl_*`` kwargs for the orchestrator.

    With ``client_ca`` set the server **requires and verifies** a client certificate
    signed by that CA (mutual TLS), so only enrolled workers can reach the control plane.
    """
    kwargs: dict[str, Any] = {
        "ssl_certfile": _require_file(cert, "TLS cert (--tls-cert)"),
        "ssl_keyfile": _require_file(key, "TLS key (--tls-key)"),
    }
    if client_ca:
        kwargs["ssl_ca_certs"] = _require_file(client_ca, "client CA (--tls-client-ca)")
        kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
    return kwargs


def build_server_context(
    cert: str,
    key: str,
    client_ca: str | None = None,
) -> ssl.SSLContext:
    """Build a server ``SSLContext`` expressing the same policy as :func:`server_ssl_kwargs`.

    uvicorn builds its own equivalent context from ``server_ssl_kwargs``; this helper is for
    embedding or testing the exact same behavior. With ``client_ca`` set it requires and
    verifies client certificates (mutual TLS).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(_require_file(cert, "TLS cert"), _require_file(key, "TLS key"))
    if client_ca:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(_require_file(client_ca, "client CA"))
    return ctx
