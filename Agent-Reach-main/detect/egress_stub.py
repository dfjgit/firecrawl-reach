"""
Minimal egress-proxy stub for testing the sidecar's proxy chaining.

Not part of the stealth stack — a test double that plays the role of the
upstream/egress proxy and logs every tunnel it establishes, so tests can
prove traffic really flowed through it.

Usage:
    python detect/egress_stub.py http   [port]   # HTTP CONNECT proxy
    python detect/egress_stub.py socks5 [port]   # SOCKS5 proxy (no auth)

Prints "LISTEN <addr>" on the first stdout line, then one
"<mode> CONNECT <host:port>" line per tunnel. Auth, if required by the
client, is accepted unconditionally (test double).
"""

import socket
import struct
import sys
import threading

CHUNK = 65536


def relay(a: socket.socket, b: socket.socket) -> None:
    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(CHUNK)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    threading.Thread(target=pump, args=(a, b), daemon=True).start()
    pump(b, a)
    a.close()
    b.close()


def log(mode: str, target: str) -> None:
    print(f"{mode} CONNECT {target}", flush=True)


def _read_headers(conn: socket.socket, limit: int = 65536) -> bytes:
    """Reads until the blank line; returns the raw header block. Never
    over-reads, so post-header bytes stay on the socket for the relay."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(1)
        if not chunk or len(buf) > limit:
            raise ConnectionError("bad header block")
        buf += chunk
    return buf


def handle_http(conn: socket.socket) -> None:
    header = _read_headers(conn).decode("latin-1")
    request_line = header.split("\r\n", 1)[0]
    parts = request_line.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT":
        conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        conn.close()
        return
    target = parts[1]
    host, _, port = target.rpartition(":")
    try:
        upstream = socket.create_connection((host, int(port)), timeout=15)
    except OSError:
        conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        conn.close()
        return
    log("http", target)
    conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    relay(conn, upstream)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("eof")
        buf += chunk
    return buf


def handle_socks5(conn: socket.socket) -> None:
    _, nmethods = _recv_exact(conn, 2)
    methods = _recv_exact(conn, nmethods)
    if 0x02 in methods:  # username/password offered -> accept any
        conn.sendall(b"\x05\x02")
        _, ulen = _recv_exact(conn, 2)
        _recv_exact(conn, ulen)
        plen = _recv_exact(conn, 1)[0]
        _recv_exact(conn, plen)
        conn.sendall(b"\x01\x00")
    elif 0x00 in methods:
        conn.sendall(b"\x05\x00")
    else:
        conn.sendall(b"\x05\xFF")
        conn.close()
        return
    _, cmd, _, atyp = _recv_exact(conn, 4)
    if atyp == 0x01:
        host = socket.inet_ntoa(_recv_exact(conn, 4))
    elif atyp == 0x04:
        host = socket.inet_ntop(socket.AF_INET6, _recv_exact(conn, 16))
    else:
        host = _recv_exact(conn, _recv_exact(conn, 1)[0]).decode("idna")
    (port,) = struct.unpack(">H", _recv_exact(conn, 2))
    if cmd != 0x01:
        conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        conn.close()
        return
    try:
        upstream = socket.create_connection((host, port), timeout=15)
    except OSError:
        conn.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
        conn.close()
        return
    log("socks5", f"{host}:{port}")
    conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    relay(conn, upstream)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "http"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    handler = {"http": handle_http, "socks5": handle_socks5}.get(mode)
    if handler is None:
        print(__doc__)
        sys.exit(2)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)
    print(f"LISTEN {srv.getsockname()[0]}:{srv.getsockname()[1]}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_guard, args=(handler, conn), daemon=True).start()


def _guard(handler, conn: socket.socket) -> None:
    try:
        handler(conn)
    except (OSError, ConnectionError):
        try:
            conn.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
