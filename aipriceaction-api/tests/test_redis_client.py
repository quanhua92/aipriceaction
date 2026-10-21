from redis.connection import SSLConnection
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from aipriceaction_api.redis_client import (
    create_redis_client,
    is_redis_transport_failure,
    is_snapshot_command_miss,
)


def test_missing_or_invalid_redis_url_disables_optional_cache():
    assert create_redis_client(None) is None
    assert create_redis_client("") is None
    assert create_redis_client("not a redis URL") is None
    assert create_redis_client("redis://localhost:6379/notdb") is None
    assert create_redis_client("redis://localhost:6379/256") is None
    assert create_redis_client("redis://localhost:6379/0/1") is None
    assert create_redis_client("redis://localhost:6379/%30") is None
    assert create_redis_client("redis://user@/") is None
    assert create_redis_client("redis://%FF@127.0.0.1:6379/0") is None
    assert create_redis_client("redis://user:%FF@127.0.0.1:6379/0") is None
    assert create_redis_client("redis://:pass%FF@127.0.0.1:6379/0") is None
    assert create_redis_client("redis-cluster://127.0.0.1:6379") is None
    assert create_redis_client(
        "redis-sentinel://127.0.0.1:26379/0?sentinelServiceName=mymaster"
    ) is None


def test_fred_url_trimming_and_centralized_fallback_scheme_are_preserved():
    client = create_redis_client("  http://localhost:6383/4  ")
    assert client is not None
    try:
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 6383
        assert kwargs["db"] == 4
        assert kwargs.get("ssl", False) is False
    finally:
        client.close()

    trimmed = create_redis_client("redis://localhost:6379/0 ")
    assert trimmed is not None
    trimmed.close()


def test_explicit_zero_redis_port_is_not_replaced_by_the_library_default():
    client = create_redis_client("redis://127.0.0.1:0/1")
    assert client is not None
    try:
        assert client.connection_pool.connection_kwargs["host"] == "127.0.0.1"
        assert client.connection_pool.connection_kwargs["port"] == 0
        assert client.connection_pool.connection_kwargs["db"] == 1
    finally:
        client.close()


def test_uppercase_redis_scheme_is_accepted_like_rust_url_parsing():
    client = create_redis_client("REDIS://localhost:6380/2")
    assert client is not None
    try:
        assert client.connection_pool.connection_kwargs["host"] == "localhost"
        assert client.connection_pool.connection_kwargs["port"] == 6380
        assert client.connection_pool.connection_kwargs["db"] == 2
    finally:
        client.close()


def test_valkey_schemes_map_to_the_same_centralized_redis_transports():
    client = create_redis_client("valkey://user:p%40ss@localhost:6381/3")
    assert client is not None
    try:
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == 6381
        assert kwargs["db"] == 3
        assert kwargs["username"] == "user"
        assert kwargs["password"] == "p@ss"
        assert kwargs.get("ssl", False) is False
    finally:
        client.close()

    tls_client = create_redis_client("valkeys://localhost:6382/0")
    assert tls_client is not None
    try:
        assert tls_client.connection_pool.connection_class is SSLConnection
    finally:
        tls_client.close()


def test_redis_query_parameters_are_ignored_like_fred_centralized_config():
    client = create_redis_client(
        "redis://localhost:6380/0?db=1&node=alternate.example:6381"
    )
    assert client is not None
    try:
        assert client.connection_pool.connection_kwargs["port"] == 6380
        assert client.connection_pool.connection_kwargs["db"] == 0
        assert "node" not in client.connection_pool.connection_kwargs
    finally:
        client.close()


def test_redis_transport_errors_are_distinguished_from_server_command_errors():
    assert is_redis_transport_failure(RedisConnectionError("offline"))
    assert is_redis_transport_failure(RedisTimeoutError("timed out"))
    assert not is_redis_transport_failure(ResponseError("WRONGTYPE"))


def test_snapshot_command_miss_classification_matches_fred_error_kinds():
    assert is_snapshot_command_miss(ResponseError("WRONGTYPE snapshot key"))
    assert is_snapshot_command_miss(ResponseError("Invalid argument(s) for command"))
    assert is_snapshot_command_miss(ResponseError("Invalid command"))
    assert not is_snapshot_command_miss(ResponseError("NOPERM this user has no permissions"))
    assert not is_snapshot_command_miss(ResponseError("ERR command failed"))
    assert not is_snapshot_command_miss(RedisConnectionError("offline"))
