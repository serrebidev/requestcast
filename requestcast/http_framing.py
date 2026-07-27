"""Deterministic response framing for RequestCast's local web interface."""

from __future__ import annotations

import socket
import time
import traceback
from typing import Any, Callable, Iterable

from werkzeug.serving import WSGIRequestHandler


class RequestCastHTTP10RequestHandler(WSGIRequestHandler):
    """Write complete HTTP/1.0 responses directly to the socket on Windows.

    Werkzeug 3.x drains the request socket after every WSGI response.  On some
    Windows 11 systems a local network filter reports an empty request socket
    as readable, causing ``self.rfile.read(10_000_000)`` to block while the
    client waits for a response body that is already in the kernel send buffer.

    This handler bypasses Werkzeug's ``run_wsgi`` entirely.  It calls the WSGI
    app, buffers the full response (the middleware already does this), formats
    a single HTTP/1.0 message, and sends it with one ``socket.sendall()`` call
    followed by ``shutdown(SHUT_WR)``.  No speculative drain is performed.
    """

    protocol_version = "HTTP/1.0"

    def run_wsgi(self) -> None:
        environ = self.make_environ()

        status_holder: dict[str, Any] = {"status": None, "headers": [], "exc_info": None}
        body_parts: list[bytes] = []

        def start_response(status, headers, exc_info=None):
            if status_holder["status"] is not None and exc_info is None:
                raise AssertionError("start_response called twice without exc_info")
            status_holder["status"] = status
            status_holder["headers"] = list(headers)
            status_holder["exc_info"] = exc_info

            def write(data: bytes) -> None:
                body_parts.append(bytes(data))

            return write

        response = None
        try:
            response = self.server.app(environ, start_response)
            for chunk in response:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("WSGI response chunks must be bytes")
                body_parts.append(bytes(chunk))
        except Exception:
            self.close_connection = True
            raise
        finally:
            if response is not None and hasattr(response, "close"):
                try:
                    response.close()
                except Exception:
                    pass

        status_line = status_holder["status"] or "500 Internal Server Error"
        headers = status_holder["headers"]
        body = b"".join(body_parts)

        # Build the complete raw HTTP/1.0 response in one bytes object.
        status_code = int(str(status_line).split(" ", 1)[0])
        if status_code in {204, 304} or 100 <= status_code < 200:
            body = b""

        raw = f"HTTP/1.0 {status_line}\r\n".encode()
        for name, value in headers:
            raw += f"{name}: {value}\r\n".encode()
        if not any(name.casefold() == "connection" for name, _ in headers):
            raw += b"Connection: close\r\n"
        raw += b"\r\n"
        raw += body

        self.close_connection = True
        try:
            self.connection.sendall(raw)
            self.connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class BufferedClosingMiddleware:
    """Buffer each response and send it with an exact byte length.

    RequestCast is a local, single-user application whose HTML and JSON responses are
    small. Fully buffering them avoids client hangs caused by ambiguous end-of-body
    framing or network-filter software that mishandles chunked transfer encoding.
    """

    def __init__(
        self,
        application: Callable[..., Iterable[bytes]],
        log: Callable[[str], None] | None = None,
        *,
        add_connection_close: bool = True,
    ) -> None:
        self.application = application
        self.log = log or (lambda _message: None)
        self.add_connection_close = add_connection_close

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        started = time.monotonic()
        method = str(environ.get("REQUEST_METHOD") or "?").upper()
        path = str(environ.get("PATH_INFO") or "/")
        remote = str(environ.get("REMOTE_ADDR") or "?")
        host = str(environ.get("HTTP_HOST") or "?")
        user_agent = str(environ.get("HTTP_USER_AGENT") or "")[:300]
        captured: dict[str, Any] = {
            "status": None,
            "headers": [],
            "exc_info": None,
        }
        body_parts: list[bytes] = []

        def captured_start_response(
            status: str,
            headers: list[tuple[str, str]],
            exc_info: Any = None,
        ) -> Callable[[bytes], None]:
            if captured["status"] is not None and exc_info is None:
                raise AssertionError("start_response called twice without exc_info")
            captured["status"] = status
            captured["headers"] = list(headers)
            captured["exc_info"] = exc_info

            def write(data: bytes) -> None:
                if not isinstance(data, (bytes, bytearray, memoryview)):
                    raise TypeError("WSGI write() data must be bytes")
                body_parts.append(bytes(data))

            return write

        self.log(
            f"REQUEST start method={method} path={path!r} remote={remote!r} "
            f"host={host!r} user_agent={user_agent!r}"
        )

        response: Iterable[bytes] | None = None
        try:
            response = self.application(environ, captured_start_response)
            for chunk in response:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("WSGI response chunks must be bytes")
                body_parts.append(bytes(chunk))
        except BaseException:
            self.log(
                f"RESPONSE exception method={method} path={path!r} "
                f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}\n"
                f"{traceback.format_exc()}"
            )
            raise
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    self.log(f"RESPONSE close exception method={method} path={path!r}")

        status = captured["status"]
        if not status:
            raise RuntimeError("WSGI application returned without calling start_response")

        complete_body = b"".join(body_parts)
        status_code = int(str(status).split(" ", 1)[0])
        original_headers = list(captured["headers"])
        original_length = next(
            (value for name, value in original_headers if name.casefold() == "content-length"),
            None,
        )
        headers = [
            (name, value)
            for name, value in original_headers
            if name.casefold() not in {"content-length", "transfer-encoding", "connection"}
        ]

        if method == "HEAD":
            body = b""
            content_length = original_length or str(len(complete_body))
        elif 100 <= status_code < 200 or status_code in {204, 304}:
            body = b""
            content_length = "0"
        else:
            body = complete_body
            content_length = str(len(body))

        headers.append(("Content-Length", content_length))
        if self.add_connection_close:
            headers.append(("Connection", "close"))
        start_response(status, headers, captured["exc_info"])

        connection_detail = "middleware-close" if self.add_connection_close else "server-close"
        self.log(
            f"RESPONSE finish method={method} path={path!r} status={status!r} "
            f"bytes={len(body)} content_length={content_length!r} "
            f"connection={connection_detail!r} "
            f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}"
        )
        return [body]
