"""Deterministic response framing for RequestCast's local web interface."""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable, Iterable

from werkzeug.serving import WSGIRequestHandler


class RequestCastHTTP10RequestHandler(WSGIRequestHandler):
    """Disable persistent HTTP connections and chunked transfer on Windows."""

    protocol_version = "HTTP/1.0"


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
