"""Tests for the shared TLS/mTLS transport helpers (``trust.tls``).

Includes a real mutual-TLS handshake: a stdlib HTTPS server built from
``build_server_context`` is exercised by an ``httpx`` client built from ``build_client``,
proving the server accepts a correctly-signed client cert and rejects a connection with
none. Certificates are generated in-process with ``cryptography`` (the pinned trust root).
"""

from __future__ import annotations

import datetime
import ipaddress
import pathlib
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from trust import build_client, build_server_context, client_ssl_params, server_ssl_kwargs
from trust.tls import _require_file

# --- unit tests: parameter wiring, no sockets ---------------------------------------------

def test_client_params_default_is_system_trust_no_client_cert() -> None:
    assert client_ssl_params() == {"verify": True}


def test_client_params_pins_ca(tmp_path) -> None:
    ca_key, ca_cert = _gen_ca()
    ca = tmp_path / "ca.pem"
    ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    params = client_ssl_params(ca_cert=str(ca))
    assert isinstance(params["verify"], ssl.SSLContext)


def test_client_params_requires_both_cert_and_key(tmp_path) -> None:
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    with pytest.raises(ValueError, match="BOTH"):
        client_ssl_params(client_cert=str(cert))
    with pytest.raises(ValueError, match="BOTH"):
        client_ssl_params(client_key=str(cert))


def test_client_params_missing_file_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        client_ssl_params(ca_cert="/no/such/ca.pem")


def test_server_ssl_kwargs_without_client_ca_has_no_mtls(tmp_path) -> None:
    cert, key = tmp_path / "s.crt", tmp_path / "s.key"
    cert.write_text("x")
    key.write_text("y")
    kwargs = server_ssl_kwargs(str(cert), str(key))
    assert kwargs["ssl_certfile"] == str(cert)
    assert kwargs["ssl_keyfile"] == str(key)
    assert "ssl_cert_reqs" not in kwargs and "ssl_ca_certs" not in kwargs


def test_server_ssl_kwargs_with_client_ca_requires_client_cert(tmp_path) -> None:
    cert, key, ca = tmp_path / "s.crt", tmp_path / "s.key", tmp_path / "ca.crt"
    for f in (cert, key, ca):
        f.write_text("x")
    kwargs = server_ssl_kwargs(str(cert), str(key), client_ca=str(ca))
    assert kwargs["ssl_ca_certs"] == str(ca)
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED


def test_require_file_message() -> None:
    with pytest.raises(ValueError, match="thing not found"):
        _require_file("/nope", "thing")


# --- integration: real mutual-TLS handshake -----------------------------------------------

def _gen_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OneCompute Test CA")])
    now = datetime.datetime.now(datetime.UTC)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _gen_leaf(ca_key, ca_cert, common_name, san=None, eku=None):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage(eku or [ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return key, builder.sign(ca_key, hashes.SHA256())


def _write_pem(path, key=None, cert=None) -> None:
    data = b""
    if cert is not None:
        data += cert.public_bytes(serialization.Encoding.PEM)
    if key is not None:
        data += key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    path.write_bytes(data)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence test server logging
        pass


@pytest.fixture
def mtls_server(tmp_path):
    ca_key, ca_cert = _gen_ca()
    srv_key, srv_cert = _gen_leaf(
        ca_key, ca_cert, "localhost",
        san=[x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")],
    )
    cli_key, cli_cert = _gen_leaf(ca_key, ca_cert, "worker-1", eku=[ExtendedKeyUsageOID.CLIENT_AUTH])

    ca_pem = tmp_path / "ca.pem"
    srv_crt, srv_keyf = tmp_path / "srv.crt", tmp_path / "srv.key"
    cli_crt, cli_keyf = tmp_path / "cli.crt", tmp_path / "cli.key"
    _write_pem(ca_pem, cert=ca_cert)
    _write_pem(srv_crt, cert=srv_cert)
    _write_pem(srv_keyf, key=srv_key)
    _write_pem(cli_crt, cert=cli_cert)
    _write_pem(cli_keyf, key=cli_key)

    ctx = build_server_context(str(srv_crt), str(srv_keyf), client_ca=str(ca_pem))
    httpd = HTTPServer(("127.0.0.1", 0), _OkHandler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield {
            "url": f"https://127.0.0.1:{port}",
            "ca": str(ca_pem),
            "client_cert": str(cli_crt),
            "client_key": str(cli_keyf),
        }
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_mutual_tls_accepts_valid_client_cert(mtls_server) -> None:
    with build_client(
        mtls_server["url"],
        ca_cert=mtls_server["ca"],
        client_cert=mtls_server["client_cert"],
        client_key=mtls_server["client_key"],
        timeout=5.0,
    ) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_mutual_tls_rejects_missing_client_cert(mtls_server) -> None:
    # Pin the CA (so the server cert verifies) but present NO client cert: the mTLS server
    # must refuse the handshake.
    with build_client(mtls_server["url"], ca_cert=mtls_server["ca"], timeout=5.0) as client:
        with pytest.raises(httpx.TransportError):
            client.get("/")


def test_tls_rejects_untrusted_server_cert(mtls_server) -> None:
    # A different CA that never signed the server cert: verification must fail even though we
    # still present a valid client cert.
    _, other_ca_cert = _gen_ca()
    bogus = pathlib.Path(mtls_server["ca"]).parent / "bogus_ca.pem"
    bogus.write_bytes(other_ca_cert.public_bytes(serialization.Encoding.PEM))
    with build_client(
        mtls_server["url"],
        ca_cert=str(bogus),
        client_cert=mtls_server["client_cert"],
        client_key=mtls_server["client_key"],
        timeout=5.0,
    ) as client:
        with pytest.raises(httpx.TransportError):
            client.get("/")
