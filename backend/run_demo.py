"""run_demo.py -- start NightWatch over HTTPS so phones can use the camera.

    python run_demo.py                  # normal demo (Wi-Fi check ON)
    python run_demo.py --no-wifi-check  # e.g. rehearsing without the hotspot

What it does:
1. Makes a self-signed HTTPS certificate in backend/certs/ the first time (and again only
   if the laptop has a new IP address it doesn't cover).
2. Prints the address to open on phones.
3. Points parent email links at that address, then starts the server on port 8000.

Phones will show a "not secure / not private" warning once, because the certificate is
self-made. Tap Advanced -> Proceed. The camera works after that.
"""
import datetime
import ipaddress
import logging
import os
import socket
import sys

import uvicorn

PORT = 8000
HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(HERE, "certs")
CERT, KEY = os.path.join(CERT_DIR, "cert.pem"), os.path.join(CERT_DIR, "key.pem")
WINDOWS_HOTSPOT_IP = "192.168.137.1"


def local_ips() -> list[str]:
    """IPv4 addresses of this laptop (hotspot, Wi-Fi, Ethernet)."""
    ips = set()
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ips.add(info[4][0])
    try:  # the address used for outgoing traffic, in case the hostname lookup misses it
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def cert_covers(ips: list[str]) -> bool:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        return False
    from cryptography import x509
    with open(CERT, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    return set(ips) <= covered


def make_cert(ips: list[str]):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NightWatch demo")])
    all_ips = sorted(set(ips) | {"127.0.0.1", WINDOWS_HOTSPOT_IP})
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in all_ips])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))
    os.makedirs(CERT_DIR, exist_ok=True)
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Made a new HTTPS certificate for: localhost, {', '.join(all_ips)}")


class HideConnectionReset(logging.Filter):
    """On Windows, Python prints a scary ConnectionResetError traceback every time a phone
    or browser closes an HTTPS connection. It's harmless, so keep it out of the demo terminal."""
    def filter(self, record):
        return not (record.exc_info and isinstance(record.exc_info[1], ConnectionResetError))


def main():
    logging.getLogger("asyncio").addFilter(HideConnectionReset())
    ips = local_ips()
    if not cert_covers(ips):
        make_cert(ips)

    # Phones on the Windows hotspot reach the laptop at 192.168.137.1.
    public_ip = WINDOWS_HOTSPOT_IP if WINDOWS_HOTSPOT_IP in ips else (ips[0] if ips else "127.0.0.1")
    os.environ.setdefault("BASE_URL", f"https://{public_ip}:{PORT}")   # used in parent email links
    if "--no-wifi-check" in sys.argv:
        os.environ["ENFORCE_NETWORK"] = "false"

    print("\nNightWatch is starting.")
    print(f"  On this laptop:  https://localhost:{PORT}")
    for ip in ips:
        note = "  <- phones on the laptop hotspot use this" if ip == WINDOWS_HOTSPOT_IP else ""
        print(f"  On a phone:      https://{ip}:{PORT}{note}")
    if WINDOWS_HOTSPOT_IP not in ips:
        print("  (Laptop hotspot is off. Turn on Mobile hotspot in Windows settings for the Wi-Fi demo.)")
    print(f"  Wi-Fi check: {'OFF' if os.environ.get('ENFORCE_NETWORK') == 'false' else 'ON'}")
    print("  First visit on each phone: tap Advanced -> Proceed on the certificate warning.\n")

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, ssl_keyfile=KEY, ssl_certfile=CERT)


if __name__ == "__main__":
    os.chdir(HERE)   # so main.py, the database and the frontend path resolve from anywhere
    main()
