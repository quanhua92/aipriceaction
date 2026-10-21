"""HTTP transport behavior that mirrors tower-http's default compression layer."""

import importlib.util
import re
import zlib

_QVALUE = re.compile(r"q=([01])(?:\.([0-9]{0,3}))?\Z", re.IGNORECASE)
_HAS_BROTLI = importlib.util.find_spec("brotli") is not None


def _quality(value):
    match = _QVALUE.fullmatch(value)
    if match is None:
        return None
    whole = int(match.group(1))
    fraction = match.group(2) or ""
    if whole == 1 and any(digit != "0" for digit in fraction):
        return None
    return whole * 1000 + int(fraction.ljust(3, "0") or "0")


def _accepted_encoding(headers):
    """Pick the highest-q encoding, preferring br, gzip, deflate, then identity."""
    accepted = {}
    ranks = {"identity": 0, "deflate": 1, "gzip": 2, "br": 3}
    for key, raw_value in headers:
        if key.lower() != b"accept-encoding":
            continue
        try:
            values = raw_value.decode("ascii").split(",")
        except UnicodeDecodeError:
            continue
        for item in values:
            parts = item.split(";", 1)
            token = parts[0].strip().lower()
            if token == "x-gzip":
                token = "gzip"
            if token not in ranks or (token == "br" and not _HAS_BROTLI):
                continue
            quality = 1000 if len(parts) == 1 else _quality(parts[1].strip())
            if quality is None:
                continue
            accepted[token] = max(accepted.get(token, -1), quality)

    candidates = [(quality, ranks[token], token) for token, quality in accepted.items()]
    candidates = [candidate for candidate in candidates if candidate[0] > 0]
    if not candidates:
        return "identity"
    return max(candidates)[2]


def _header_values(headers, name):
    return [value for key, value in headers if key.lower() == name]


def _compressible(headers):
    if _header_values(headers, b"content-encoding") or _header_values(headers, b"content-range"):
        return False
    content_types = _header_values(headers, b"content-type")
    content_type = content_types[0] if content_types else b""
    if (
        (content_type.startswith(b"image/") and content_type != b"image/svg+xml")
        or content_type.startswith(b"application/grpc")
        or content_type.startswith(b"text/event-stream")
    ):
        return False
    lengths = _header_values(headers, b"content-length")
    if lengths:
        try:
            raw_size = lengths[-1].decode("ascii")
            size = int(raw_size) if re.fullmatch(r"[0-9]+", raw_size) else None
        except (UnicodeDecodeError, ValueError):
            size = None
        if size is not None and size < (1 << 64) and size < 32:
            return False
    return True


def _vary_has_accept_encoding(headers):
    for value in _header_values(headers, b"vary"):
        if b"accept-encoding" in value.lower():
            return True
    return False


def _set_header(headers, name, value):
    headers = [(key, current) for key, current in headers if key.lower() != name]
    headers.append((name, value))
    return headers


def _prepend_vary(headers, value):
    existing = _header_values(headers, b"vary")
    combined = b", ".join((value, *existing)) if existing else value
    return _set_header(headers, b"vary", combined)


class RustCompatibleCompressionMiddleware:
    """ASGI middleware matching tower-http's default 32-byte compression policy."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        encoding = _accepted_encoding(scope.get("headers", ()))
        state = {"compressor": None, "encoding": encoding}

        async def send_compressed(message):
            message_type = message["type"]
            if message_type == "http.response.start":
                headers = list(message.get("headers", ()))
                status = message.get("status", 200)
                # The router dispatches HEAD through GET handlers. Dynamic
                # responses still pass through Tower compression before the
                # HTTP server suppresses their payload; ServeDir handles HEAD
                # itself and does not add compression headers.
                is_head = scope.get("aipa.original_method") == "HEAD"
                path = scope.get("path", "")
                is_static_head = is_head and (path == "/public" or path.startswith("/public/"))
                compressible = status not in {204, 304} and _compressible(headers)
                if compressible and not is_static_head and not _vary_has_accept_encoding(headers):
                    headers = _prepend_vary(headers, b"accept-encoding")
                if compressible and not is_static_head and encoding != "identity":
                    headers = [
                        (key, value)
                        for key, value in headers
                        if key.lower() not in {b"content-length", b"accept-ranges"}
                    ]
                    headers = _set_header(headers, b"content-encoding", encoding.encode("ascii"))
                    if encoding == "gzip":
                        state["compressor"] = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
                    elif encoding == "deflate":
                        state["compressor"] = zlib.compressobj(wbits=zlib.MAX_WBITS)
                    elif encoding == "br":
                        import brotli

                        # tower-http explicitly overrides Brotli's library
                        # default (quality 11) with quality 4. The library
                        # maximum made large API responses take seconds to
                        # compress and diverged from Rust's default behavior.
                        state["compressor"] = brotli.Compressor(quality=4)
                message = {**message, "headers": headers}
                await send(message)
                return

            compressor = state["compressor"]
            if message_type == "http.response.body" and compressor is not None:
                body = message.get("body", b"")
                if state["encoding"] == "br":
                    encoded = compressor.process(body)
                    if not message.get("more_body", False):
                        encoded += compressor.finish()
                else:
                    encoded = compressor.compress(body)
                    if not message.get("more_body", False):
                        encoded += compressor.flush(zlib.Z_FINISH)
                message = {**message, "body": encoded}
            if message_type == "http.response.body" and scope.get("aipa.original_method") == "HEAD":
                message = {**message, "body": b""}
            await send(message)

        await self.app(scope, receive, send_compressed)
