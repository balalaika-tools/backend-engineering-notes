"""Resolve the immutable manuals and vocabularies used by one investigation."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError
from worker.domain.control_context import ControlContext, ControlManual, DecodeValue
from worker.ports.control_context.config_generation import ConfigGenerationSource
from worker.ports.control_context.config_summarizer import ConfigSummarizer
from worker.ports.control_context.control_config_source import (
    ControlConfigSource,
    InvalidControlConfigError,
)
from worker.ports.control_context.control_context_cache import (
    ControlContextCache,
    ControlContextCacheKey,
    ControlContextCacheUnavailableError,
)
from worker.ports.control_context.manual_store import ManualKey, ManualStore

logger = logging.getLogger(__name__)


class VocabularyUnavailableError(RuntimeError):
    """A required decode-set vocabulary is missing or empty."""

    error_code = "vocabulary_unavailable"


class ControlContextRebuildTimeoutError(RuntimeError):
    """Another worker did not publish the shared context within the wait budget."""

    error_code = "control_context_cache_unavailable"


@dataclass(frozen=True, slots=True)
class ControlContextCachePolicy:
    rebuild_lease_seconds: float
    rebuild_wait_seconds: float
    poll_interval_seconds: float

    def __post_init__(self) -> None:
        if self.rebuild_lease_seconds <= 0:
            raise ValueError("Control-context rebuild lease must be positive")
        if self.rebuild_wait_seconds <= 0:
            raise ValueError("Control-context rebuild wait must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("Control-context cache poll interval must be positive")
        if self.rebuild_wait_seconds <= self.rebuild_lease_seconds:
            raise ValueError("Control-context rebuild wait must exceed the rebuild lease")


_CONTEXT_CODEC = TypeAdapter(ControlContext)


class ResolveControlContext:
    def __init__(
        self,
        *,
        generation_source: ConfigGenerationSource,
        context_cache: ControlContextCache,
        config_source: ControlConfigSource,
        manual_store: ManualStore,
        summarizer: ConfigSummarizer,
        tenant_token: str,
        positions_control: str,
        reference_control: str,
        prompt_version: str,
        deployment_release_id: str,
        cache_policy: ControlContextCachePolicy,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._generation_source = generation_source
        self._context_cache = context_cache
        self._config_source = config_source
        self._manual_store = manual_store
        self._summarizer = summarizer
        self._tenant_token = tenant_token
        self._positions_control = positions_control
        self._reference_control = reference_control
        self._prompt_version = prompt_version
        self._deployment_release_id = deployment_release_id
        self._cache_policy = cache_policy
        self._sleep = sleep
        self._monotonic = monotonic

    async def resolve(self) -> ControlContext:
        generation = await self._generation_source.get_generation()
        key = ControlContextCacheKey(
            tenant_token=self._tenant_token,
            deployment_release_id=self._deployment_release_id,
            generation=generation,
        )
        deadline = self._monotonic() + self._cache_policy.rebuild_wait_seconds
        while True:
            cached = await self._read_cached(key)
            if cached is not None:
                return cached
            lease = await self._context_cache.try_acquire_rebuild(
                key,
                lease_seconds=self._cache_policy.rebuild_lease_seconds,
            )
            if lease is not None:
                try:
                    cached = await self._read_cached(key)
                    if cached is None:
                        cached = await self._build(generation)
                        await self._context_cache.put(
                            key,
                            _CONTEXT_CODEC.dump_json(cached),
                            lease=lease,
                        )
                except BaseException:
                    try:
                        await self._context_cache.release_rebuild(lease)
                    except Exception:
                        logger.exception("control_context_rebuild_release_failed")
                    raise
                else:
                    await self._context_cache.release_rebuild(lease)
                    return cached
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ControlContextRebuildTimeoutError(
                    "Timed out waiting for the shared control context"
                )
            await self._sleep(min(self._cache_policy.poll_interval_seconds, remaining))

    async def _read_cached(self, key: ControlContextCacheKey) -> ControlContext | None:
        payload = await self._context_cache.get(key)
        if payload is None:
            return None
        try:
            context = _CONTEXT_CODEC.validate_json(payload)
        except (ValidationError, ValueError) as exc:
            raise ControlContextCacheUnavailableError(
                "The shared control context is invalid"
            ) from exc
        if context.generation != key.generation:
            raise ControlContextCacheUnavailableError(
                "The shared control context has an unexpected generation"
            )
        return context

    async def _build(self, generation: int) -> ControlContext:
        config_tasks = (
            asyncio.create_task(
                self._config_source.fetch_configuration(
                    tenant_token=self._tenant_token,
                    control_name=self._positions_control,
                )
            ),
            asyncio.create_task(
                self._config_source.fetch_configuration(
                    tenant_token=self._tenant_token,
                    control_name=self._reference_control,
                )
            ),
            asyncio.create_task(
                self._config_source.fetch_decode_sets(tenant_token=self._tenant_token)
            ),
        )
        try:
            positions_config, reference_config, decode_sets = await asyncio.gather(*config_tasks)
        except BaseException:
            await _cancel_tasks(config_tasks)
            raise
        manual_tasks = (
            asyncio.create_task(
                self._resolve_manual(
                    self._positions_control,
                    positions_config.md5,
                    positions_config.xml,
                )
            ),
            asyncio.create_task(
                self._resolve_manual(
                    self._reference_control,
                    reference_config.md5,
                    reference_config.xml,
                )
            ),
        )
        try:
            positions, reference = await asyncio.gather(*manual_tasks)
        except BaseException:
            await _cancel_tasks(manual_tasks)
            raise
        vocabularies = {decode_set.name: decode_set.values for decode_set in decode_sets}
        reason_codes = _required_vocabulary(vocabularies, "ReasonCodes")
        resolution_codes = _required_vocabulary(vocabularies, "ResolutionCodes")
        return ControlContext(
            generation=generation,
            positions=positions,
            reference=reference,
            reason_codes=reason_codes,
            resolution_codes=resolution_codes,
            positions_rec_schema=positions_config.rec_schema_name,
            positions_tenant_schema=positions_config.tenant_schema_name,
            reference_rec_schema=reference_config.rec_schema_name,
            reference_tenant_schema=reference_config.tenant_schema_name,
            reason_code_feature_id=_required_feature_identifier(
                positions_config.editable_feature_ids,
                "ExceptionReasonCode",
            ),
            resolution_code_feature_id=_required_feature_identifier(
                positions_config.editable_feature_ids,
                "ExceptionResolutionCode",
            ),
        )

    async def _resolve_manual(self, control_name: str, md5: str, xml: bytes) -> ControlManual:
        key = ManualKey(
            tenant_token=self._tenant_token,
            control_name=control_name,
            config_md5=md5,
            prompt_version=self._prompt_version,
        )
        content = await self._manual_store.get(key)
        if content is None:
            content = await self._summarizer.summarize(control_name=control_name, xml=xml)
            await self._manual_store.put(key, content)
        return ControlManual(control_name=control_name, config_md5=md5, content=content)


def _required_vocabulary(
    vocabularies: dict[str, tuple[DecodeValue, ...]],
    name: str,
) -> tuple[DecodeValue, ...]:
    vocabulary = vocabularies.get(name)
    if not vocabulary:
        raise VocabularyUnavailableError(f"Required CTC decode set {name} is missing or empty")
    return vocabulary


def _required_feature_identifier(
    features: tuple[tuple[str, str], ...],
    name: str,
) -> str:
    identifier = dict(features).get(name)
    if not identifier:
        raise InvalidControlConfigError(f"Required editable CTC feature {name} is missing")
    return identifier


async def _cancel_tasks(tasks: tuple[asyncio.Task[Any], ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
