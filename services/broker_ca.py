"""Nexus v2.4 — Broker CA: self-signed mTLS certificate authority."""
from __future__ import annotations

import ipaddress
import logging
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("nexus.broker_ca")

_CA_KEY = "broker_ca.key"
_CA_CERT = "broker_ca.pem"
_SERVER_KEY = "broker_server.key"
_SERVER_CERT = "broker_server.pem"

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    x509 = None


def _have_crypto() -> bool:
    return x509 is not None


def init_broker_ca(cert_dir: Path) -> bool:
    if not _have_crypto():
        log.warning("cryptography library not available — broker CA disabled")
        return False

    cert_dir.mkdir(parents=True, exist_ok=True)
    ca_key_path = cert_dir / _CA_KEY
    ca_cert_path = cert_dir / _CA_CERT

    if ca_key_path.exists() and ca_cert_path.exists():
        log.info("Broker CA already exists at %s", cert_dir)
        _ensure_server_cert(cert_dir)
        return True

    log.info("Generating broker CA in %s", cert_dir)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pr0t31n"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Broker CA"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_key_path.write_bytes(
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    os.chmod(ca_key_path, 0o600)
    ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    _ensure_server_cert(cert_dir)
    log.info("Broker CA generated")
    return True


def _ensure_server_cert(cert_dir: Path):
    srv_key_path = cert_dir / _SERVER_KEY
    srv_cert_path = cert_dir / _SERVER_CERT
    if srv_key_path.exists() and srv_cert_path.exists():
        return

    ca_key = serialization.load_pem_private_key(
        (cert_dir / _CA_KEY).read_bytes(), password=None
    )
    ca_cert = x509.load_pem_x509_certificate((cert_dir / _CA_CERT).read_bytes())

    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pr0t31n"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Broker"),
    ])
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(srv_name)
        .issuer_name(ca_cert.subject)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("*.local"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
            ]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    srv_key_path.write_bytes(
        srv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    os.chmod(srv_key_path, 0o600)
    srv_cert_path.write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    log.info("Broker server certificate generated")


def issue_agent_cert(cert_dir: Path, agent_hostname: str) -> dict:
    if not _have_crypto():
        return {"ok": False, "error": "cryptography not available"}

    ca_key = serialization.load_pem_private_key(
        (cert_dir / _CA_KEY).read_bytes(), password=None
    )
    ca_cert = x509.load_pem_x509_certificate((cert_dir / _CA_CERT).read_bytes())

    agent_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agent_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pr0t31n"),
        x509.NameAttribute(NameOID.COMMON_NAME, agent_hostname),
    ])
    agent_cert = (
        x509.CertificateBuilder()
        .subject_name(agent_name)
        .issuer_name(ca_cert.subject)
        .public_key(agent_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    return {
        "ok": True,
        "cert_pem": agent_cert.public_bytes(serialization.Encoding.PEM).decode(),
        "key_pem": agent_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode(),
        "ca_pem": (cert_dir / _CA_CERT).read_text(),
    }


def get_ssl_context(cert_dir: Path) -> ssl.SSLContext | None:
    srv_cert = cert_dir / _SERVER_CERT
    srv_key = cert_dir / _SERVER_KEY
    ca_cert = cert_dir / _CA_CERT

    if not all(p.exists() for p in (srv_cert, srv_key, ca_cert)):
        return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(srv_cert), keyfile=str(srv_key))
    ctx.load_verify_locations(cafile=str(ca_cert))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
