"""run_demo.py -- start NightWatch over HTTPS so phones can use the camera.

    python run_demo.py                  # normal demo (campus Wi-Fi check ON)
    python run_demo.py --no-wifi-check  # e.g. rehearsing off campus
    python run_demo.py --pretend 23:45  # daytime demo: server acts as if it's 23:45 now
    python run_demo.py --pretend 23:45 --reset   # same, plus fresh demo data (seed.py --history)

The laptop and the phones should all be on the campus Wi-Fi (BITS-Student).
To show a refusal, put a phone on the laptop's own hotspot: that isn't campus Wi-Fi.

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


def clock_offset_for(hhmm: str) -> int:
    """Seconds to add to real time so the server's clock reads hhmm right now."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(os.getenv("CAMPUS_TZ", "Asia/Dubai"))
    real = datetime.datetime.now(tz)
    h, m = map(int, hhmm.split(":"))
    target = real.replace(hour=h, minute=m, second=0, microsecond=0)
    if h < 12 and real.hour >= 12:   # "00:10" during an afternoon demo = just after midnight tonight
        target += datetime.timedelta(days=1)
    return int((target - real).total_seconds())


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

    if "--no-wifi-check" in sys.argv:
        os.environ["ENFORCE_NETWORK"] = "false"
    pretend = None
    if "--pretend" in sys.argv:
        pretend = sys.argv[sys.argv.index("--pretend") + 1]
        os.environ["CLOCK_OFFSET_SECONDS"] = str(clock_offset_for(pretend))
    if "--reset" in sys.argv:   # fresh demo data, with history that ends just before "tonight"
        import subprocess
        subprocess.run([sys.executable, "seed.py", "--history"], check=True)
    from config import CAMPUS_NETWORKS
    from night import on_campus_network

    # Phones on BITS-Student reach the laptop at its campus address.
    campus_ips = [ip for ip in ips if on_campus_network(ip)]
    public_ip = campus_ips[0] if campus_ips else (ips[0] if ips else "127.0.0.1")
    os.environ.setdefault("BASE_URL", f"https://{public_ip}:{PORT}")   # used in parent email links

    print("\nNightWatch is starting.")
    print(f"  On this laptop:  https://localhost:{PORT}")
    for ip in ips:
        if ip in campus_ips:
            note = "  <- phones on BITS-Student use this"
        elif ip == WINDOWS_HOTSPOT_IP:
            note = "  <- laptop hotspot: NOT campus Wi-Fi, check-in is refused here"
        else:
            note = ""
        print(f"  On a phone:      https://{ip}:{PORT}{note}")
    if not campus_ips:
        print("  (This laptop isn't on the campus Wi-Fi. Connect it to BITS-Student so phones can reach it.)")
    check = "OFF" if os.environ.get("ENFORCE_NETWORK") == "false" else "ON"
    print(f"  Campus Wi-Fi check: {check} (campus = {', '.join(CAMPUS_NETWORKS)})")
    if pretend:
        print(f"  Demo clock: the server is pretending it's {pretend} now (real time keeps ticking from there)")
    print("  First visit on each phone: tap Advanced -> Proceed on the certificate warning.\n")

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, ssl_keyfile=KEY, ssl_certfile=CERT)


if __name__ == "__main__":
    os.chdir(HERE)   # so main.py, the database and the frontend path resolve from anywhere
    main()
