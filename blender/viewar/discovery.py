# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Bonjour (mDNS) advertising so the app finds Blender automatically."""

import socket
import threading

try:
    from zeroconf import ServiceInfo, Zeroconf

    AVAILABLE = True
except Exception:  # wheel not bundled or failed to load
    AVAILABLE = False

SERVICE_TYPE = "_viewar._tcp.local."


def local_ip():
    """Best guess at this machine's LAN address. Sends no packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def local_addresses():
    """All IPv4 addresses a phone could reach, best guess first.

    Computers with Ethernet plus Wi-Fi, or a VPN, have several, and the
    best guess is not always the one the phone shares a network with.
    """
    primary = local_ip()
    found = []
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith(("127.", "169.254.")):
                    found.append(ip.ip)
    except Exception:  # ifaddr not bundled or the OS query failed
        pass
    ordered = [primary] if primary != "127.0.0.1" else []
    ordered += [a for a in found if a not in ordered]
    return ordered or ["127.0.0.1"]


class Advertiser:
    def __init__(self):
        self._lock = threading.Lock()
        self._zeroconf = None
        self._info = None
        self._stopped = False
        self.active = False
        self.error = None

    def start(self, port, addresses):
        if not AVAILABLE:
            self.error = "zeroconf is not bundled"
            return
        # Registration blocks for about a second, so keep it off the UI thread.
        threading.Thread(
            target=self._register, args=(port, addresses), daemon=True
        ).start()

    def _register(self, port, addresses):
        zc = None
        try:
            host = socket.gethostname().split(".")[0] or "blender"
            info = ServiceInfo(
                SERVICE_TYPE,
                f"ViewAR on {host}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(a) for a in addresses],
                port=port,
                properties={"v": "1"},
                server=f"{host}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info, allow_name_change=True)
        except Exception as exc:
            self.error = str(exc)
            print(f"ViewAR: auto discovery failed: {exc}")
            if zc is not None:
                zc.close()
            return
        with self._lock:
            if self._stopped:
                _close(zc, info)
                return
            self._zeroconf, self._info = zc, info
            self.active = True

    def stop(self):
        with self._lock:
            self._stopped = True
            zc, info = self._zeroconf, self._info
            self._zeroconf = self._info = None
            self.active = False
        if zc is not None:
            threading.Thread(target=_close, args=(zc, info), daemon=True).start()


def _close(zc, info):
    try:
        zc.unregister_service(info)
    except Exception:
        pass
    finally:
        zc.close()
