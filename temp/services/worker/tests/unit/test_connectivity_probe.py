"""Private connectivity probe output-safety tests."""

import json
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace, TracebackType
from typing import Any

import httpx
import pytest
from nats.js.api import ConsumerConfig, StreamConfig
from nats.js.errors import NotFoundError
from pydantic import SecretStr
from worker import connectivity_probe as probe_module
from worker.connectivity_probe import ConnectivityProbe, ProbeConfig, run_probe_steps


def _config() -> ProbeConfig:
    return ProbeConfig(
        expected_egress_ip="34.248.87.217",
        ctc_issuer_url="https://issuer.example.test/token",
        ctc_api_base_url="https://api.example.test",
        ctc_client_id="client-id",
        ctc_client_secret=SecretStr("client-secret"),
        ctc_database_url="postgresql+asyncpg://reader@db.example.test:5432/ctc",
        ctc_db_password=SecretStr("database-secret"),
        nats_url="tls://nats.example.test:4222",
        nats_auth_token=SecretStr("nats-secret"),
        nats_stream="EXCEPTION_INVESTIGATIONS",
        nats_consumer="INVESTIGATION_WORKERS",
        nats_subject="investigations.requested",
    )


@pytest.mark.asyncio
async def test_failures_identify_the_probe_without_leaking_secrets() -> None:
    emitted: list[str] = []
    password = "database-password-never-log"
    token = "oauth-token-never-log"

    async def healthy() -> None:
        return None

    async def fail_database() -> None:
        raise RuntimeError(f"connection with {password} and {token} failed")

    exit_code = await run_probe_steps(
        [
            ("fixed_egress", healthy),
            ("peer_postgresql_tls", fail_database),
            ("jetstream", healthy),
        ],
        emit=emitted.append,
    )

    assert exit_code == 1
    assert [json.loads(event)["check"] for event in emitted] == [
        "fixed_egress",
        "peer_postgresql_tls",
    ]
    output = "\n".join(emitted)
    assert password not in output
    assert token not in output
    assert "RuntimeError" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_check",
    ["fixed_egress", "ctc_oauth", "ctc_inventory", "peer_dns", "jetstream"],
)
async def test_each_failed_probe_has_a_distinct_safe_identifier(failed_check: str) -> None:
    emitted: list[str] = []

    async def fail() -> None:
        raise OSError("sensitive endpoint details")

    exit_code = await run_probe_steps([(failed_check, fail)], emit=emitted.append)

    assert exit_code == 1
    assert json.loads(emitted[0]) == {
        "check": failed_check,
        "error_type": "OSError",
        "status": "failed",
    }


@pytest.mark.asyncio
async def test_wrong_observed_egress_ip_fails() -> None:
    emitted: list[str] = []
    probe = ConnectivityProbe(_config())
    await probe.close()
    probe._http = httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="203.0.113.10\n"))
    )
    try:
        exit_code = await run_probe_steps(
            [("fixed_egress", probe.check_fixed_egress)],
            emit=emitted.append,
        )
    finally:
        await probe.close()

    assert exit_code == 1
    assert json.loads(emitted[0]) == {
        "check": "fixed_egress",
        "error_type": "RuntimeError",
        "status": "failed",
    }


class FakeResult:
    def scalar_one(self) -> int:
        return 2


class FakeConnection:
    async def execute(self, _statement: object) -> FakeResult:
        return FakeResult()


class FakeConnectionContext(AbstractAsyncContextManager[FakeConnection]):
    async def __aenter__(self) -> FakeConnection:
        return FakeConnection()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None


class FakeEngine:
    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext()

    async def dispose(self) -> None:
        return None


class EmptyJetStream:
    def __init__(self) -> None:
        self.stream_config: StreamConfig | None = None
        self.consumer_config: ConsumerConfig | None = None

    async def stream_info(self, _stream: str) -> None:
        raise NotFoundError

    async def add_stream(self, *, config: StreamConfig) -> SimpleNamespace:
        self.stream_config = config
        return SimpleNamespace(config=config)

    async def consumer_info(self, _stream: str, _consumer: str) -> None:
        raise NotFoundError

    async def add_consumer(
        self,
        _stream: str,
        *,
        config: ConsumerConfig,
    ) -> SimpleNamespace:
        self.consumer_config = config
        return SimpleNamespace(config=config)


class FakeNatsClient:
    def __init__(self, jetstream: EmptyJetStream) -> None:
        self._jetstream = jetstream
        self.closed = False

    def jetstream(self) -> EmptyJetStream:
        return self._jetstream

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_postgresql_probe_requires_select_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[str] = []

    def fake_engine(*_args: Any, **_kwargs: Any) -> FakeEngine:
        return FakeEngine()

    monkeypatch.setattr(probe_module, "create_async_engine", fake_engine)
    probe = ConnectivityProbe(_config())
    try:
        exit_code = await run_probe_steps(
            [("peer_postgresql_tls", probe.check_peer_postgresql_tls)],
            emit=emitted.append,
        )
    finally:
        await probe.close()

    assert exit_code == 1
    assert json.loads(emitted[0]) == {
        "check": "peer_postgresql_tls",
        "error_type": "RuntimeError",
        "status": "failed",
    }


@pytest.mark.asyncio
async def test_jetstream_probe_authenticates_and_initializes_the_dev_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jetstream = EmptyJetStream()
    client = FakeNatsClient(jetstream)
    options: dict[str, object] = {}

    async def connect(url: str, **kwargs: object) -> FakeNatsClient:
        options["url"] = url
        options.update(kwargs)
        return client

    monkeypatch.setattr(probe_module.nats, "connect", connect)
    probe = ConnectivityProbe(_config())
    try:
        await probe.check_jetstream()
    finally:
        await probe.close()

    assert options["token"] == "nats-secret"
    assert jetstream.stream_config is not None
    assert jetstream.stream_config.name == "EXCEPTION_INVESTIGATIONS"
    assert jetstream.stream_config.num_replicas == 1
    assert jetstream.consumer_config is not None
    assert jetstream.consumer_config.durable_name == "INVESTIGATION_WORKERS"
    assert client.closed is True
