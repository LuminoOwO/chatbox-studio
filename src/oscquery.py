"""
OSCQuery advertising, so VRChat finds this app by itself.

VRChat normally sends avatar parameters (mute, AFK, ...) only to UDP port 9001. If another
app already uses 9001 (an OSC router like VOR, face tracking, ...), we listen on a free port
instead and announce it with OSCQuery: an mDNS service plus a tiny HTTP server that says
"send /avatar/parameters to this UDP port". VRChat then sends to us as well.

Same approach as VRChat's own vrc-oscquery-lib and the VrcAdvert tool.
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


def _tree() -> dict:
    return {
        "DESCRIPTION": "root node", "FULL_PATH": "/", "ACCESS": 0,
        "CONTENTS": {
            "avatar": {
                "FULL_PATH": "/avatar", "ACCESS": 0,
                "CONTENTS": {
                    "parameters": {"DESCRIPTION": "", "FULL_PATH": "/avatar/parameters",
                                   "ACCESS": 2, "TYPE": "b"},
                },
            },
        },
    }


class OSCQueryService:
    def __init__(self, name: str, osc_port: int, log: Callable[[str], None] = lambda m: None):
        self.name = f"{name}-{uuid.uuid4().hex[:4].upper()}"     # unique per run
        self.osc_port = osc_port
        self.log = log
        self.http: Optional[ThreadingHTTPServer] = None
        self.zc = None
        self.infos = []

    def host_info(self) -> dict:
        return {"NAME": self.name, "OSC_IP": "127.0.0.1", "OSC_PORT": self.osc_port,
                "OSC_TRANSPORT": "UDP",
                "EXTENSIONS": {"ACCESS": True, "CLIPMODE": False, "RANGE": True,
                               "TYPE": True, "VALUE": True}}

    def start(self):
        svc = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if "HOST_INFO" in self.path:
                    body = svc.host_info()
                else:
                    body = _tree()
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        http_port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True, name="oscquery").start()

        from zeroconf import ServiceInfo, Zeroconf
        self.zc = Zeroconf()
        addr = [socket.inet_aton("127.0.0.1")]
        self.infos = [
            ServiceInfo("_oscjson._tcp.local.", f"{self.name}._oscjson._tcp.local.",
                        addresses=addr, port=http_port, server=f"{self.name}.local."),
            ServiceInfo("_osc._udp.local.", f"{self.name}._osc._udp.local.",
                        addresses=addr, port=self.osc_port, server=f"{self.name}.local."),
        ]
        for info in self.infos:
            self.zc.register_service(info)
        return http_port

    def stop(self):
        try:
            if self.zc is not None:
                for info in self.infos:
                    self.zc.unregister_service(info)
                self.zc.close()
        except Exception:
            pass
        self.zc = None
        if self.http is not None:
            self.http.shutdown()
            self.http.server_close()
            self.http = None


def _local_ips() -> set:
    ips = {"127.0.0.1"}
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    ips.add(a.address)
    except Exception:
        pass
    return ips


class VRChatFinder:
    """Finds the UDP port VRChat really listens on (it announces it with OSCQuery as
    'VRChat-Client-XXXXXX' under _osc._udp). Normally 9000, but different when VRChat was
    started with --osc=... (common with OSC routers like VOR) or 9000 was taken.
    Only VRChat on this PC counts, not other people's VRChat on the same network."""

    def __init__(self, on_change: Callable[[Optional[int]], None],
                 log: Callable[[str], None] = lambda m: None):
        self.on_change = on_change
        self.log = log
        self.port: Optional[int] = None
        self.zc = None
        self.browser = None
        self._names: dict = {}

    def start(self):
        from zeroconf import ServiceBrowser, Zeroconf
        self.zc = Zeroconf()
        self.browser = ServiceBrowser(self.zc, "_osc._udp.local.", handlers=[self._on_event])

    def _on_event(self, zeroconf, service_type, name, state_change):
        if not name.lower().startswith("vrchat-client"):
            return
        kind = getattr(state_change, "name", str(state_change))
        if kind == "Removed":
            self._names.pop(name, None)
            self._update()
            return
        threading.Thread(target=self._resolve, args=(service_type, name), daemon=True).start()

    def _resolve(self, service_type, name):
        try:
            info = self.zc.get_service_info(service_type, name, timeout=3000)
        except Exception:
            info = None
        if info is None or not info.port:
            return
        addrs = set(info.parsed_addresses()) if hasattr(info, "parsed_addresses") else set()
        if addrs and not (addrs & _local_ips()):
            return                                   # someone else's VRChat on the network
        self._names[name] = int(info.port)
        self._update()

    def _update(self):
        port = next(iter(self._names.values()), None)
        if port != self.port:
            self.port = port
            try:
                self.on_change(port)
            except Exception:
                pass

    def stop(self):
        try:
            if self.browser is not None:
                self.browser.cancel()
            if self.zc is not None:
                self.zc.close()
        except Exception:
            pass
        self.zc = self.browser = None
