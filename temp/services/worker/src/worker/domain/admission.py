"""Deterministic AIMD admission decisions and LLM-attempt windows."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AimdPolicy:
    min_samples: int
    stable_threshold: float
    decrease_threshold: float
    break_threshold: float
    decrease_factor: float
    min_inflight: int
    max_per_instance: int


@dataclass(frozen=True, slots=True)
class WindowObservation:
    total: int
    failures: int
    retry_after_seconds: float | None = None

    @property
    def failure_rate(self) -> float:
        return self.failures / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class AimdDecision:
    target: int
    failure_rate: float
    enter_cooldown: bool


class WindowCounters:
    """Event-loop-local counters updated once for every provider attempt."""

    def __init__(self) -> None:
        self._total = 0
        self._failures = 0
        self._retry_after_seconds: float | None = None

    def record(self, *, failed: bool, retry_after_seconds: float | None = None) -> None:
        self._total += 1
        if not failed:
            return
        self._failures += 1
        if retry_after_seconds is not None:
            current = self._retry_after_seconds or 0.0
            self._retry_after_seconds = max(current, retry_after_seconds)

    def snapshot_and_reset(self) -> WindowObservation:
        observation = WindowObservation(
            total=self._total,
            failures=self._failures,
            retry_after_seconds=self._retry_after_seconds,
        )
        self._total = 0
        self._failures = 0
        self._retry_after_seconds = None
        return observation


class AdmissionState:
    def __init__(self, *, initial_target: int, max_per_instance: int) -> None:
        self._target = initial_target
        self._max_per_instance = max_per_instance
        self._actual_inflight = 0

    @property
    def target(self) -> int:
        return self._target

    @property
    def actual_inflight(self) -> int:
        return self._actual_inflight

    @property
    def limit(self) -> int:
        return min(self._target, self._max_per_instance)

    @property
    def can_admit(self) -> bool:
        return self._actual_inflight < self.limit

    def set_target(self, target: int) -> None:
        self._target = target

    def admitted(self) -> None:
        self._actual_inflight += 1

    def completed(self) -> None:
        if self._actual_inflight == 0:
            raise RuntimeError("Cannot complete an investigation when none are in flight")
        self._actual_inflight -= 1


def decide_aimd(
    *,
    current_target: int,
    observation: WindowObservation,
    policy: AimdPolicy,
    increase_step: int,
) -> AimdDecision:
    rate = observation.failure_rate
    if observation.total == 0:
        return AimdDecision(current_target, rate, False)
    if observation.total < policy.min_samples:
        if observation.failures == 0:
            target = min(current_target + increase_step, policy.max_per_instance)
            return AimdDecision(target, rate, False)
        return AimdDecision(current_target, rate, False)
    if rate > policy.break_threshold:
        return AimdDecision(policy.min_inflight, rate, True)
    if rate > policy.decrease_threshold:
        target = max(policy.min_inflight, int(current_target * policy.decrease_factor))
        return AimdDecision(target, rate, False)
    if rate < policy.stable_threshold:
        target = min(current_target + increase_step, policy.max_per_instance)
        return AimdDecision(target, rate, False)
    return AimdDecision(current_target, rate, False)


def cooldown_ttl_seconds(
    *,
    default_seconds: float,
    jitter_factor: float,
    retry_after_seconds: float | None,
) -> float:
    if retry_after_seconds is not None:
        return min(max(0.0, retry_after_seconds), default_seconds)
    return default_seconds * jitter_factor
