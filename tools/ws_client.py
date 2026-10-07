"""A WebSocket client in the standard library, for the tests.

Enough of RFC 6455 to open a connection and read server frames, so the tests
can run under FreeCAD's Python with nothing installed. Not a general client:
it never fragments, and it only masks what it sends because the protocol
insists clients do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClient(object):
    def __init__(self, host: str, port: int, path: str = "/ws", timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        self.expected_accept = self._handshake(host, port, path)

    def _handshake(self, host, port, path) -> str:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET {path} HTTP/1.1\r\n"
            "Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(path=path, host=host, port=port, key=key)
        self.sock.sendall(request.encode())

        while b"\r\n\r\n" not in self._buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("server closed during the handshake")
            self._buffer += chunk
        head, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        self.response = head.decode("latin-1")
        return base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()
        ).decode()

    @property
    def upgraded(self) -> bool:
        return (
            self.response.split("\r\n")[0].find("101") >= 0
            and self.expected_accept in self.response
        )

    def _need(self, count: int) -> None:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AssertionError("server closed mid frame")
            self._buffer += chunk

    def read_frame(self):
        """(opcode, payload bytes, was_masked) for one frame."""
        self._need(2)
        opcode = self._buffer[0] & 0x0F
        masked = bool(self._buffer[1] & 0x80)
        length = self._buffer[1] & 0x7F
        offset = 2
        if length == 126:
            self._need(4)
            length = struct.unpack(">H", self._buffer[2:4])[0]
            offset = 4
        elif length == 127:
            self._need(10)
            length = struct.unpack(">Q", self._buffer[2:10])[0]
            offset = 10
        if masked:
            self._need(offset + 4)
            mask = self._buffer[offset:offset + 4]
            offset += 4
        self._need(offset + length)
        payload = self._buffer[offset:offset + length]
        self._buffer = self._buffer[offset + length:]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload, masked

    def read_json(self) -> dict:
        opcode, payload, _ = self.read_frame()
        return json.loads(payload.decode("utf-8"))

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
