"""Fleet-shared, generation-aware control context resolution."""

import asyncio
import time
from collections.abc import Callable

import pytest
from worker.application.resolve_control_context import (
    ControlContextCachePolicy,
    ControlContextRebuildTimeoutError,
    ResolveControlContext,
    VocabularyUnavailableError,
)
from worker.domain.control_context import DecodeSet, DecodeValue
from worker.ports.control_context.control_config_source import (
    ControlConfiguration,
)
from worker.ports.control_context.control_context_cache import (
    ControlContextCacheKey,
    ControlContextCacheUnavailableError,
    ControlContextRebuildLease,
)
from worker.ports.control_context.manual_store import ManualKey


def _configuration(name: str, version: int = 1) -> ControlConfiguration:
    return ControlConfiguration(
        xml=f"<{name} version='{version}' />".encode(),
        md5=f"{name}-{version}",
        rec_schema_name=f"webui{name.title()}",
        tenant_schema_name="webui",
        editable_feature_ids=(
            (
                ("ExceptionReasonCode", "reason-feature-id"),
                ("ExceptionResolutionCode", "resolution-feature-id"),
            )
            if name == "positions"
            else ()
        ),
    )


class FakeGenerationSource:
    def __init__(self) -> None:
        self.generation = 0
        self.calls = 0

    async def get_generation(self) -> int:
        self.calls += 1
        return self.generation


class FakeConfigSource:
    def __init__(self) -> None:
        self.configurations = {
            "Positions": _configuration("positions"),
            "Reference": _configuration("reference"),
        }
        self.configuration_calls: list[str] = []
        self.decode_calls = 0
        self.decode_sets = _decode_sets()
        self.yield_during_fetch = False

    async def fetch_configuration(
        self,
        *,
        tenant_token: str,
        control_name: str,
    ) -> ControlConfiguration:
        assert tenant_token == "WEBUI"
        self.configuration_calls.append(control_name)
        if self.yield_during_fetch:
            await asyncio.sleep(0)
        return self.configurations[control_name]

    async def fetch_decode_sets(self, *, tenant_token: str) -> tuple[DecodeSet, ...]:
        assert tenant_token == "WEBUI"
        self.decode_calls += 1
        if self.yield_during_fetch:
            await asyncio.sleep(0)
        return self.decode_sets


class FakeManualStore:
    def __init__(self) -> None:
        self.objects: dict[ManualKey, str] = {}
        self.lookups: list[ManualKey] = []

    async def get(self, key: ManualKey) -> str | None:
        self.lookups.append(key)
        return self.objects.get(key)

    async def put(self, key: ManualKey, content: str) -> None:
        self.objects[key] = content


class FakeSummarizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    async def summarize(self, *, control_name: str, xml: bytes) -> str:
        self.calls.append((control_name, xml))
        return f"manual for {control_name}: {xml.decode()}"


class FakeContextCache:
    def __init__(self) -> None:
        self.payloads: dict[ControlContextCacheKey, bytes] = {}
        self.owners: dict[ControlContextCacheKey, str] = {}
        self.error: Exception | None = None
        self.puts = 0
        self._owner_sequence = 0

    async def get(self, key: ControlContextCacheKey) -> bytes | None:
        self._raise_if_unavailable()
        return self.payloads.get(key)

    async def put(
        self,
        key: ControlContextCacheKey,
        payload: bytes,
        *,
        lease: ControlContextRebuildLease,
    ) -> None:
        self._raise_if_unavailable()
        if self.owners.get(key) != lease.owner_token:
            raise ControlContextCacheUnavailableError("rebuild lease lost")
        self.payloads[key] = payload
        self.puts += 1

    async def try_acquire_rebuild(
        self,
        key: ControlContextCacheKey,
        *,
        lease_seconds: float,
    ) -> ControlContextRebuildLease | None:
        del lease_seconds
        self._raise_if_unavailable()
        if key in self.owners:
            return None
        self._owner_sequence += 1
        owner = f"builder-{self._owner_sequence}"
        self.owners[key] = owner
        return ControlContextRebuildLease(key=key, owner_token=owner)

    async def release_rebuild(self, lease: ControlContextRebuildLease) -> None:
        self._raise_if_unavailable()
        if self.owners.get(lease.key) == lease.owner_token:
            del self.owners[lease.key]

    def clear(self) -> None:
        self.payloads.clear()

    def _raise_if_unavailable(self) -> None:
        if self.error is not None:
            raise self.error


def _decode_sets(*, include_resolution: bool = True) -> tuple[DecodeSet, ...]:
    sets = [
        DecodeSet(
            name="ReasonCodes",
            description=None,
            values=(DecodeValue(code="reason", name="Reason", description="Why"),),
        )
    ]
    if include_resolution:
        sets.append(
            DecodeSet(
                name="ResolutionCodes",
                description=None,
                values=(DecodeValue(code="resolution", name="Resolution", description="How"),),
            )
        )
    return tuple(sets)


def _resolver(
    generation: FakeGenerationSource,
    source: FakeConfigSource,
    store: FakeManualStore,
    summarizer: FakeSummarizer,
    cache: FakeContextCache,
    *,
    release_id: str = "release-1",
    monotonic: Callable[[], float] = time.monotonic,
) -> ResolveControlContext:
    return ResolveControlContext(
        generation_source=generation,
        context_cache=cache,
        config_source=source,
        manual_store=store,
        summarizer=summarizer,
        tenant_token="WEBUI",
        positions_control="Positions",
        reference_control="Reference",
        prompt_version="v1",
        deployment_release_id=release_id,
        cache_policy=ControlContextCachePolicy(
            rebuild_lease_seconds=1,
            rebuild_wait_seconds=2,
            poll_interval_seconds=0.001,
        ),
        monotonic=monotonic,
    )


def test_cache_policy_rejects_wait_that_cannot_outlive_the_rebuild_lease() -> None:
    with pytest.raises(ValueError, match="wait must exceed"):
        ControlContextCachePolicy(
            rebuild_lease_seconds=60,
            rebuild_wait_seconds=60,
            poll_interval_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_new_worker_reuses_the_fleet_context_after_a_pod_restart() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()

    first = await _resolver(generation, source, store, summarizer, cache).resolve()
    after_restart = await _resolver(generation, source, store, summarizer, cache).resolve()

    assert after_restart == first
    assert after_restart.reason_codes[0].description == "Why"
    assert after_restart.resolution_codes[0].code == "resolution"
    assert after_restart.positions_rec_schema == "webuiPositions"
    assert after_restart.positions_tenant_schema == "webui"
    assert after_restart.reference_rec_schema == "webuiReference"
    assert after_restart.reference_tenant_schema == "webui"
    assert source.configuration_calls == ["Positions", "Reference"]
    assert source.decode_calls == 1
    assert len(summarizer.calls) == 2
    assert cache.puts == 1


@pytest.mark.asyncio
async def test_reset_refetches_context_but_reuses_unchanged_s3_manuals() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    resolver = _resolver(generation, source, store, summarizer, cache)
    await resolver.resolve()

    generation.generation = 1
    context = await resolver.resolve()

    assert context.generation == 1
    assert source.decode_calls == 2
    assert len(source.configuration_calls) == 4
    assert len(summarizer.calls) == 2
    assert cache.puts == 2


@pytest.mark.asyncio
async def test_new_release_refreshes_context_once_without_resummarising() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    await _resolver(generation, source, store, summarizer, cache).resolve()

    await _resolver(
        generation,
        source,
        store,
        summarizer,
        cache,
        release_id="release-2",
    ).resolve()

    assert source.decode_calls == 2
    assert len(source.configuration_calls) == 4
    assert len(summarizer.calls) == 2
    assert cache.puts == 2


@pytest.mark.asyncio
async def test_changed_md5_generates_only_the_changed_manual() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    resolver = _resolver(generation, source, store, summarizer, cache)
    await resolver.resolve()

    source.configurations["Positions"] = _configuration("positions", version=2)
    generation.generation = 1
    context = await resolver.resolve()

    assert context.positions.config_md5 == "positions-2"
    assert len(summarizer.calls) == 3
    assert {key.config_md5 for key in store.objects} == {
        "positions-1",
        "positions-2",
        "reference-1",
    }


@pytest.mark.asyncio
async def test_missing_resolution_codes_does_not_publish_an_invalid_context() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    source.decode_sets = _decode_sets(include_resolution=False)
    cache = FakeContextCache()
    resolver = _resolver(
        generation,
        source,
        FakeManualStore(),
        FakeSummarizer(),
        cache,
    )

    with pytest.raises(VocabularyUnavailableError) as error:
        await resolver.resolve()

    assert error.value.error_code == "vocabulary_unavailable"
    assert cache.payloads == {}
    assert cache.owners == {}


@pytest.mark.asyncio
async def test_concurrent_workers_share_one_fleet_wide_rebuild() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    source.yield_during_fetch = True
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    resolvers = [_resolver(generation, source, store, summarizer, cache) for _ in range(20)]

    contexts = await asyncio.gather(*(resolver.resolve() for resolver in resolvers))

    assert all(context == contexts[0] for context in contexts)
    assert source.configuration_calls == ["Positions", "Reference"]
    assert source.decode_calls == 1
    assert len(summarizer.calls) == 2
    assert cache.puts == 1


@pytest.mark.asyncio
async def test_cache_failure_does_not_fall_back_to_per_process_work() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    cache.error = ControlContextCacheUnavailableError("redis unavailable")

    with pytest.raises(ControlContextCacheUnavailableError):
        await _resolver(
            generation,
            source,
            FakeManualStore(),
            summarizer,
            cache,
        ).resolve()

    assert source.configuration_calls == []
    assert summarizer.calls == []


@pytest.mark.asyncio
async def test_exhausted_rebuild_wait_is_transient_without_duplicate_work() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    cache = FakeContextCache()
    key = ControlContextCacheKey("WEBUI", "release-1", 0)
    cache.owners[key] = "another-worker"
    clock = iter((0.0, 3.0))

    with pytest.raises(ControlContextRebuildTimeoutError) as error:
        await _resolver(
            generation,
            source,
            FakeManualStore(),
            FakeSummarizer(),
            cache,
            monotonic=lambda: next(clock),
        ).resolve()

    assert error.value.error_code == "control_context_cache_unavailable"
    assert source.configuration_calls == []


@pytest.mark.asyncio
async def test_redis_data_loss_rebuilds_from_ctc_and_s3_without_an_llm_call() -> None:
    generation = FakeGenerationSource()
    source = FakeConfigSource()
    store = FakeManualStore()
    summarizer = FakeSummarizer()
    cache = FakeContextCache()
    await _resolver(generation, source, store, summarizer, cache).resolve()
    cache.clear()

    rebuilt = await _resolver(generation, source, store, summarizer, cache).resolve()

    assert rebuilt.positions.content.startswith("manual for Positions")
    assert source.decode_calls == 2
    assert len(source.configuration_calls) == 4
    assert len(summarizer.calls) == 2
    assert cache.puts == 2
