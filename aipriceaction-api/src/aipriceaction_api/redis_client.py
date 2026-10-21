"""Optional redis-py client construction for Rust-compatible cache reads."""

import logging
import re
from urllib.parse import unquote_to_bytes, urlsplit

log = logging.getLogger(__name__)
_REDIS_DB_PATH = re.compile(r"\+?[0-9]+\Z", re.ASCII)
_URL_EDGE_TRIM = "".join(chr(codepoint) for codepoint in range(0x21))


def _rust_redis_url(redis_url: str):
    """Normalize a Rust-accepted centralized URL for redis-py."""
    try:
        # url::Url::parse trims leading/trailing C0 controls and spaces before
        # parsing; urllib.parse.urlsplit does not trim every such character.
        redis_url = redis_url.strip(_URL_EDGE_TRIM)
        parsed = urlsplit(redis_url)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    # fred dispatches these suffixes to topologies this centralized client does
    # not implement. Its unix-socket feature is disabled in this Cargo build.
    if scheme.endswith(("-cluster", "-sentinel", "+unix")) or not parsed.hostname:
        return None

    # fred percent-decodes credentials as UTF-8 and rejects invalid byte
    # sequences. redis-py's URL parser replaces those bytes, which can leave a
    # Python cache client active for a URL Rust treats as invalid.
    try:
        for component in (parsed.username, parsed.password):
            if component is not None:
                unquote_to_bytes(component).decode("utf-8")
    except UnicodeDecodeError:
        return None

    path = parsed.path
    if path not in ("", "/"):
        if not path.startswith("/") or "/" in path[1:]:
            return None
        database = path[1:]
        if _REDIS_DB_PATH.fullmatch(database) is None or int(database, 10) > 255:
            return None

    # fred's centralized config reads the database only from the path and
    # ignores all query parameters (including `db` and topology `node`).
    # fred's centralized fallback accepts any URL scheme, selecting TLS when
    # the scheme starts with rediss or valkeys. redis-py only accepts Redis's
    # registered names, so map both documented Valkey and Fred's permissive
    # fallback schemes to the equivalent redis/rediss transport.
    tls = scheme.startswith(("rediss", "valkeys"))
    redis_py_scheme = "rediss" if tls else "redis"
    normalized = parsed._replace(scheme=redis_py_scheme, query="", fragment="")
    return normalized.geturl(), port


def create_redis_client(redis_url: str | None):
    """Create a synchronous Redis client, or return ``None`` when unconfigured.

    Connections are opened lazily by redis-py. Timeouts match the Rust cache
    reader's two-second command budget; callers treat read failures as cache
    misses and continue through the existing durable-history path.
    """

    if not redis_url:
        return None

    from redis import Redis

    try:
        normalized = _rust_redis_url(redis_url)
        if normalized is None:
            raise ValueError("Unsupported or invalid Redis URL")
        redis_url, parsed_port = normalized
        client = Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # redis-py treats URL port 0 as absent and silently substitutes 6379.
        # Preserve an explicit port-zero destination for deterministic tests
        # and callers that need the reserved, non-listenable TCP port.
        if parsed_port == 0:
            client.connection_pool.connection_kwargs["port"] = 0
        return client
    except Exception as exc:
        # Rust disables Redis when its URL cannot be parsed. Keep the optional
        # cache from making the API fail during startup for the same reason.
        log.warning("Invalid REDIS_URL; Redis cache reads are disabled: %s", exc)
        return None


def is_redis_transport_failure(error: Exception) -> bool:
    """Tell connection failures from Redis command errors returned by the server."""

    try:
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError
    except ImportError:
        return isinstance(error, (OSError, TimeoutError))
    return isinstance(error, (RedisConnectionError, RedisTimeoutError, OSError))


def is_snapshot_command_miss(error: Exception) -> bool:
    """Return whether Rust treats this Redis reply as a per-key snapshot miss.

    fred maps WRONGTYPE and explicit INVALID argument/command replies to
    per-command misses. Other Redis errors make its snapshot batch unavailable,
    so Python must use the same data-source fallback tag.
    """

    try:
        from redis.exceptions import ResponseError
    except ImportError:
        return False
    if not isinstance(error, ResponseError):
        return False
    parts = str(error).split(maxsplit=1)
    if not parts:
        return False
    code = parts[0].upper()
    if code == "WRONGTYPE":
        return True
    return code == "INVALID" and len(parts) > 1 and parts[1].split(maxsplit=1)[0].upper() in {
        "ARGUMENT(S)",
        "ARGUMENT",
        "COMMAND",
    }
