"""Pure AIMD admission decisions."""

import pytest
from worker.domain.admission import (
    AimdPolicy,
    WindowCounters,
    WindowObservation,
    cooldown_ttl_seconds,
    decide_aimd,
)


@pytest.fixture
def policy() -> AimdPolicy:
    return AimdPolicy(
        min_samples=20,
        stable_threshold=0.01,
        decrease_threshold=0.05,
        break_threshold=0.50,
        decrease_factor=0.7,
        min_inflight=2,
        max_per_instance=50,
    )


def test_healthy_window_additively_increases_with_cap(policy: AimdPolicy) -> None:
    decision = decide_aimd(
        current_target=10,
        observation=WindowObservation(total=40, failures=0),
        policy=policy,
        increase_step=7,
    )
    capped = decide_aimd(
        current_target=48,
        observation=WindowObservation(total=40, failures=0),
        policy=policy,
        increase_step=7,
    )

    assert decision.target == 17
    assert capped.target == 50
    assert decision.enter_cooldown is False


def test_degraded_window_multiplicatively_decreases_with_floor(policy: AimdPolicy) -> None:
    decision = decide_aimd(
        current_target=10,
        observation=WindowObservation(total=40, failures=4),
        policy=policy,
        increase_step=3,
    )
    floored = decide_aimd(
        current_target=2,
        observation=WindowObservation(total=40, failures=4),
        policy=policy,
        increase_step=3,
    )

    assert decision.target == 7
    assert floored.target == 2


def test_break_window_drops_to_minimum_and_requests_cooldown(policy: AimdPolicy) -> None:
    decision = decide_aimd(
        current_target=30,
        observation=WindowObservation(total=40, failures=21),
        policy=policy,
        increase_step=3,
    )

    assert decision.target == 2
    assert decision.enter_cooldown is True


@pytest.mark.parametrize(
    ("observation", "expected_rate"),
    [
        (WindowObservation(total=10, failures=10), 1.0),
        (WindowObservation(total=100, failures=1), 0.01),
        (WindowObservation(total=100, failures=5), 0.05),
    ],
)
def test_failed_under_sampled_windows_and_threshold_band_leave_target_unchanged(
    policy: AimdPolicy,
    observation: WindowObservation,
    expected_rate: float,
) -> None:
    decision = decide_aimd(
        current_target=10,
        observation=observation,
        policy=policy,
        increase_step=3,
    )

    assert decision.target == 10
    assert decision.failure_rate == expected_rate
    assert decision.enter_cooldown is False


def test_healthy_under_sampled_window_can_ramp_from_minimum(policy: AimdPolicy) -> None:
    decision = decide_aimd(
        current_target=policy.min_inflight,
        observation=WindowObservation(total=2, failures=0),
        policy=policy,
        increase_step=3,
    )
    idle = decide_aimd(
        current_target=policy.min_inflight,
        observation=WindowObservation(total=0, failures=0),
        policy=policy,
        increase_step=3,
    )

    assert decision.target == 5
    assert idle.target == policy.min_inflight


def test_window_counters_capture_attempts_and_largest_retry_after() -> None:
    counters = WindowCounters()
    counters.record(failed=False)
    counters.record(failed=True, retry_after_seconds=2)
    counters.record(failed=True, retry_after_seconds=10)

    observation = counters.snapshot_and_reset()

    assert observation == WindowObservation(total=3, failures=2, retry_after_seconds=10)
    assert counters.snapshot_and_reset() == WindowObservation(total=0, failures=0)


def test_cooldown_ttl_caps_retry_after_and_uses_jittered_default() -> None:
    provider_ttl = cooldown_ttl_seconds(
        default_seconds=60,
        jitter_factor=0.8,
        retry_after_seconds=125,
    )
    default_ttl = cooldown_ttl_seconds(
        default_seconds=60,
        jitter_factor=1.2,
        retry_after_seconds=None,
    )

    assert provider_ttl == 60
    assert default_ttl == 72
