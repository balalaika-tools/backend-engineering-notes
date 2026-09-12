"""Private deployment probe for external and peer connectivity prerequisites."""

import asyncio
import json
import os
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import nats
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from worker.db.ctc.engine import authenticated_database_url

ProbeStep = tuple[str, Callable[[], Awaitable[None]]]


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    expected_egress_ip: str
    ctc_issuer_url: str
    ctc_api_base_url: str
    ctc_client_id: str
    ctc_client_secret: SecretStr
    ctc_database_url: str
    ctc_db_password: SecretStr
    nats_url: str
    nats_auth_token: SecretStr
    nats_stream: str
    nats_consumer: str
    nats_subject: str

    @classmethod
    def from_environment(cls) -> "ProbeConfig":
        return cls(
            expected_egress_ip=_required("EXPECTED_EGRESS_IP"),
            ctc_issuer_url=_required("CTC_ISSUER_URL"),
            ctc_api_base_url=_required("CTC_API_BASE_URL"),
            ctc_client_id=_required("CTC_CLIENT_ID"),
            ctc_client_secret=SecretStr(_required("CTC_CLIENT_SECRET")),
            ctc_database_url=_required("CTC_DATABASE_URL"),
            ctc_db_password=SecretStr(_required("CTC_DB_PASSWORD")),
            nats_url=_required("NATS_URL"),
            nats_auth_token=SecretStr(_required("NATS_AUTH_TOKEN")),
            nats_stream=_required("NATS_STREAM"),
            nats_consumer=_required("NATS_CONSUMER"),
            nats_subject=_required("NATS_SUBJECT"),
        )


class ConnectivityProbe:
    def __init__(self, config: ProbeConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(timeout=15)
        self._access_token: str | None = None

    def steps(self) -> tuple[ProbeStep, ...]:
        return (
            ("fixed_egress", self.check_fixed_egress),
            ("ctc_oauth", self.check_ctc_oauth),
            ("ctc_inventory", self.check_ctc_inventory),
            ("peer_dns", self.check_peer_dns),
            ("peer_postgresql_tls", self.check_peer_postgresql_tls),
            ("jetstream", self.check_jetstream),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def check_fixed_egress(self) -> None:
        response = await self._http.get("https://checkip.amazonaws.com")
        response.raise_for_status()
        if response.text.strip() != self._config.expected_egress_ip:
            raise RuntimeError("observed egress does not match the approved address")

    async def check_ctc_oauth(self) -> None:
        response = await self._http.post(
            self._config.ctc_issuer_url,
            auth=httpx.BasicAuth(
                self._config.ctc_client_id,
                self._config.ctc_client_secret.get_secret_value(),
            ),
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        body = response.json()
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("token response has no access token")
        self._access_token = token

    async def check_ctc_inventory(self) -> None:
        if self._access_token is None:
            raise RuntimeError("OAuth probe did not establish an access token")
        url = f"{self._config.ctc_api_base_url.rstrip('/')}/v1/inventory/controls"
        response = await self._http.get(
            url,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        response.raise_for_status()

    async def check_peer_dns(self) -> None:
        database_url = authenticated_database_url(
            self._config.ctc_database_url,
            self._config.ctc_db_password,
        )
        if database_url.host is None or database_url.port is None:
            raise RuntimeError("CTC database URL has no host or port")
        await asyncio.to_thread(
            socket.getaddrinfo,
            database_url.host,
            database_url.port,
            type=socket.SOCK_STREAM,
        )

    async def check_peer_postgresql_tls(self) -> None:
        database_url = authenticated_database_url(
            self._config.ctc_database_url,
            self._config.ctc_db_password,
        )
        engine = create_async_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
        )
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text("SELECT 1"))
                if result.scalar_one() != 1:
                    raise RuntimeError("PostgreSQL probe returned an unexpected value")
        finally:
            await engine.dispose()

    async def check_jetstream(self) -> None:
        client = await nats.connect(
            self._config.nats_url,
            token=self._config.nats_auth_token.get_secret_value(),
            connect_timeout=3,
            max_reconnect_attempts=0,
        )
        try:
            jetstream = client.jetstream()
            stream = await self._ensure_stream(jetstream)
            if stream.config.storage != StorageType.FILE:
                raise RuntimeError("JetStream storage is not file-backed")
            if (stream.config.num_replicas or 0) != 1:
                raise RuntimeError("JetStream stream does not use the dev replica count")
            consumer = await self._ensure_consumer(jetstream)
            if consumer.config.durable_name != self._config.nats_consumer:
                raise RuntimeError("JetStream durable consumer does not match")
        finally:
            await client.close()

    async def _ensure_stream(self, jetstream: Any) -> Any:
        try:
            return await jetstream.stream_info(self._config.nats_stream)
        except NotFoundError:
            return await jetstream.add_stream(
                config=StreamConfig(
                    name=self._config.nats_stream,
                    subjects=[self._config.nats_subject],
                    retention=RetentionPolicy.WORK_QUEUE,
                    storage=StorageType.FILE,
                    num_replicas=1,
                    duplicate_window=120,
                )
            )

    async def _ensure_consumer(self, jetstream: Any) -> Any:
        try:
            return await jetstream.consumer_info(
                self._config.nats_stream,
                self._config.nats_consumer,
            )
        except NotFoundError:
            return await jetstream.add_consumer(
                self._config.nats_stream,
                config=ConsumerConfig(
                    durable_name=self._config.nats_consumer,
                    ack_policy=AckPolicy.EXPLICIT,
                    ack_wait=60,
                    max_deliver=6,
                    max_ack_pending=2250,
                    filter_subject=self._config.nats_subject,
                ),
            )


async def run_probe_steps(
    steps: Sequence[ProbeStep],
    *,
    emit: Callable[[str], Any] = print,
) -> int:
    """Run ordered checks while emitting only safe check IDs and error classes."""
    for check_name, check in steps:
        try:
            await check()
        except Exception as exc:
            emit(
                json.dumps(
                    {
                        "check": check_name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    },
                    sort_keys=True,
                )
            )
            return 1
        emit(json.dumps({"check": check_name, "status": "ok"}, sort_keys=True))
    return 0


async def _run() -> int:
    probe = ConnectivityProbe(ProbeConfig.from_environment())
    try:
        return await run_probe_steps(probe.steps())
    finally:
        await probe.close()


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
