"""Per-PC local CA and HTTPS certificates. Never install trust automatically."""

from __future__ import annotations

import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_private(path: Path, key) -> None:
    with path.open("wb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    if os.name != "nt":
        path.chmod(0o600)


def ensure_certificates(directory: Path, address: str) -> dict:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    ca_path, ca_key_path = directory / "castdeck-ca.crt", directory / "ca-key.pem"
    cert_path, key_path = directory / "server-cert.pem", directory / "server-key.pem"
    now = datetime.now(timezone.utc)
    if ca_path.exists() != ca_key_path.exists():
        raise ValueError("The HTTPS authority is incomplete. Restore its certificate/key pair before continuing.")
    if ca_path.exists():
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        if ca.not_valid_after_utc <= now + timedelta(days=30):
            raise ValueError("The local HTTPS authority is expiring. Create a new authority and trust it on your devices.")
        if ca.public_key().public_numbers() != ca_key.public_key().public_numbers():
            raise ValueError("The HTTPS authority certificate and private key do not match.")
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CastDeck Local CA")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        write_private(ca_key_path, ca_key)
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    names = [x509.DNSName("localhost"), x509.DNSName("castdeck.local"),
             x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    ip = ipaddress.ip_address(address)
    if ip != ipaddress.ip_address("127.0.0.1"):
        names.append(x509.IPAddress(ip))
    reuse = False
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        existing = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        try:
            cert.verify_directly_issued_by(ca)
            reuse = (cert.not_valid_after_utc > now + timedelta(days=30)
                     and all(name in existing for name in names)
                     and cert.public_key().public_numbers() == key.public_key().public_numbers())
        except (ValueError, TypeError, InvalidSignature):
            reuse = False
    if not reuse:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "castdeck.local")]))
                .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        write_private(key_path, key)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    fingerprint = ca.fingerprint(hashes.SHA256()).hex().upper()
    return {"ca": ca_path, "cert": cert_path, "key": key_path,
            "fingerprint": ":".join(fingerprint[index:index + 2] for index in range(0, len(fingerprint), 2))}


def certificate_fingerprint(path: Path) -> str:
    fingerprint = x509.load_pem_x509_certificate(path.read_bytes()).fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(fingerprint[index:index + 2] for index in range(0, len(fingerprint), 2))
