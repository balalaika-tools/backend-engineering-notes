"""Typed, non-secret worker configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    ByteSize,
    Field,
    FiniteFloat,
    PositiveFloat,
    PositiveInt,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

EnvironmentName = Literal["local", "dev", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

YAML_POLICY_FIELDS = frozenset(
    {
        "log_level",
        "tenant_token",
        "positions_control",
        "reference_control",
        "exception_name",
        "nats_stream",
        "nats_consumer",
        "nats_subject",
        "fetch_timeout_seconds",
        "heartbeat_interval_seconds",
        "ack_wait_seconds",
        "lease_seconds",
        "max_attempts",
        "max_deliver",
        "max_ack_pending",
        "shutdown_grace_seconds",
        "duplicate_window_seconds",
        "report_inline_limit",
        "report_key_prefix",
        "manual_key_prefix",
        "platform_pool_max_size",
        "ctc_pool_max_size",
        "ctc_statement_timeout_seconds",
        "ctc_work_mem",
        "ctc_query_row_limit",
        "reconcile_interval_seconds",
        "database_retention_days",
        "window_seconds",
        "min_samples",
        "stable_threshold",
        "decrease_threshold",
        "break_threshold",
        "increase_step_min",
        "increase_step_max",
        "decrease_factor",
        "initial_target",
        "min_inflight",
        "max_per_instance",
        "llm_max_attempts",
        "llm_backoff_base_seconds",
        "llm_backoff_cap_seconds",
        "cooldown_seconds",
        "control_context_cache_prefix",
        "control_context_rebuild_lease_seconds",
        "control_context_rebuild_wait_seconds",
        "control_context_poll_interval_seconds",
        "investigation_retry_delays_seconds",
        "record_comment_limit",
        "analysis_model_call_limit",
        "analysis_tool_call_limit",
        "analysis_summarization_trigger_tokens",
        "analysis_summarization_keep_messages",
        "sql_fixer_max_attempts",
        "summarizer_prompt_version",
        "analysis_prompt_version",
        "comment_write_enabled",
        "codes_write_enabled",
    }
)


def _discover_config_dir(module_file: Path) -> Path | None:
    for parent in module_file.resolve().parents:
        candidate = parent / "config"
        if (candidate / "base.yaml").is_file():
            return candidate
    return None


_CONFIG_DIR = _discover_config_dir(Path(__file__))


def _config_dir() -> Path:
    override = os.environ.get("WORKER_CONFIG_DIR")
    if override:
        return Path(override)
    if _CONFIG_DIR is None:
        raise FileNotFoundError("Could not discover the repository-root config directory")
    return _CONFIG_DIR


def _environment_name(*sources: PydanticBaseSettingsSource) -> str:
    for source in sources:
        values = source()
        value = values.get("ENVIRONMENT_NAME") or values.get("environment_name")
        if value:
            return str(value)
    raise ValueError("ENVIRONMENT_NAME is required")


class Settings(BaseSettings):
    """Non-secret worker settings with startup-time timing validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
    )

    environment_name: EnvironmentName = Field(alias="ENVIRONMENT_NAME")
    platform_database_url: AnyUrl = Field(alias="PLATFORM_DATABASE_URL")
    ctc_database_url: AnyUrl = Field(alias="CTC_DATABASE_URL")
    nats_url: AnyUrl = Field(alias="NATS_URL")
    redis_url: AnyUrl = Field(alias="REDIS_URL")
    redis_iam_auth_enabled: bool = Field(False, alias="REDIS_IAM_AUTH_ENABLED")
    redis_iam_user_id: str | None = Field(default=None, alias="REDIS_IAM_USER_ID")
    redis_cache_name: str | None = Field(default=None, alias="REDIS_CACHE_NAME")
    redis_region: str | None = Field(default=None, alias="REDIS_REGION")
    s3_bucket: str = Field(alias="S3_BUCKET", min_length=1)
    ctc_issuer_url: AnyHttpUrl = Field(alias="CTC_ISSUER_URL")
    ctc_api_base_url: AnyHttpUrl = Field(alias="CTC_API_BASE_URL")
    ctc_client_id: str = Field(alias="CTC_CLIENT_ID", min_length=1)
    analysis_model_id: str = Field(alias="ANALYSIS_MODEL_ID", min_length=1)
    summarizer_model_id: str = Field(alias="SUMMARIZER_MODEL_ID", min_length=1)
    sql_fixer_model_id: str = Field(alias="SQL_FIXER_MODEL_ID", min_length=1)
    model_region: str = Field(alias="MODEL_REGION", min_length=1)
    s3_endpoint: AnyHttpUrl | None = Field(default=None, alias="S3_ENDPOINT")
    otlp_endpoint: AnyHttpUrl | None = Field(default=None, alias="OTLP_ENDPOINT")
    service_instance_id: str | None = Field(default=None, alias="SERVICE_INSTANCE_ID")
    deployment_release_id: str = Field(alias="DEPLOYMENT_RELEASE_ID", min_length=1)
    service_namespace: str = Field("exception-investigation", alias="SERVICE_NAMESPACE")
    service_version: str = Field("unknown", alias="SERVICE_VERSION")
    capture_ai_content: bool = Field(False, alias="CAPTURE_AI_CONTENT")
    log_full_exception_trace: bool = Field(True, alias="LOG_FULL_EXCEPTION_TRACE")
    otel_export_interval_millis: PositiveInt = Field(15_000, alias="OTEL_EXPORT_INTERVAL_MILLIS")

    log_level: LogLevel = Field(alias="LOG_LEVEL")
    tenant_token: str = Field(alias="TENANT_TOKEN", min_length=1)
    positions_control: str = Field(alias="POSITIONS_CONTROL", min_length=1)
    reference_control: str = Field(alias="REFERENCE_CONTROL", min_length=1)
    exception_name: str = Field(alias="EXCEPTION_NAME", min_length=1)
    nats_stream: str = Field(alias="NATS_STREAM", min_length=1)
    nats_consumer: str = Field(alias="NATS_CONSUMER", min_length=1)
    nats_subject: str = Field(alias="NATS_SUBJECT", min_length=1)
    fetch_timeout_seconds: PositiveFloat = Field(alias="FETCH_TIMEOUT_SECONDS")
    heartbeat_interval_seconds: PositiveFloat = Field(alias="HEARTBEAT_INTERVAL_SECONDS")
    ack_wait_seconds: PositiveFloat = Field(alias="ACK_WAIT_SECONDS")
    lease_seconds: PositiveFloat = Field(alias="LEASE_SECONDS")
    max_attempts: PositiveInt = Field(alias="MAX_ATTEMPTS")
    max_deliver: PositiveInt = Field(alias="MAX_DELIVER")
    max_ack_pending: PositiveInt = Field(alias="MAX_ACK_PENDING")
    shutdown_grace_seconds: PositiveFloat = Field(alias="SHUTDOWN_GRACE_SECONDS")
    duplicate_window_seconds: PositiveFloat = Field(alias="DUPLICATE_WINDOW_SECONDS")
    report_inline_limit: ByteSize = Field(alias="REPORT_INLINE_LIMIT")
    report_key_prefix: str = Field(alias="REPORT_KEY_PREFIX", min_length=1)
    manual_key_prefix: str = Field(alias="MANUAL_KEY_PREFIX", min_length=1)
    platform_pool_max_size: PositiveInt = Field(alias="PLATFORM_POOL_MAX_SIZE")
    ctc_pool_max_size: PositiveInt = Field(alias="CTC_POOL_MAX_SIZE")
    ctc_statement_timeout_seconds: PositiveFloat = Field(alias="CTC_STATEMENT_TIMEOUT_SECONDS")
    ctc_work_mem: ByteSize = Field(alias="CTC_WORK_MEM")
    ctc_query_row_limit: PositiveInt = Field(alias="CTC_QUERY_ROW_LIMIT")
    reconcile_interval_seconds: PositiveFloat = Field(alias="RECONCILE_INTERVAL_SECONDS")
    database_retention_days: PositiveInt = Field(alias="DATABASE_RETENTION_DAYS")
    window_seconds: PositiveFloat = Field(alias="WINDOW_SECONDS")
    min_samples: PositiveInt = Field(alias="MIN_SAMPLES")
    stable_threshold: FiniteFloat = Field(alias="STABLE_THRESHOLD", ge=0, le=1)
    decrease_threshold: FiniteFloat = Field(alias="DECREASE_THRESHOLD", ge=0, le=1)
    break_threshold: FiniteFloat = Field(alias="BREAK_THRESHOLD", ge=0, le=1)
    increase_step_min: PositiveInt = Field(alias="INCREASE_STEP_MIN")
    increase_step_max: PositiveInt = Field(alias="INCREASE_STEP_MAX")
    decrease_factor: FiniteFloat = Field(alias="DECREASE_FACTOR", gt=0, lt=1)
    initial_target: PositiveInt = Field(alias="INITIAL_TARGET")
    min_inflight: PositiveInt = Field(alias="MIN_INFLIGHT")
    max_per_instance: PositiveInt = Field(alias="MAX_PER_INSTANCE")
    llm_max_attempts: PositiveInt = Field(alias="LLM_MAX_ATTEMPTS")
    llm_backoff_base_seconds: PositiveFloat = Field(alias="LLM_BACKOFF_BASE_SECONDS")
    llm_backoff_cap_seconds: PositiveFloat = Field(alias="LLM_BACKOFF_CAP_SECONDS")
    cooldown_seconds: PositiveFloat = Field(alias="COOLDOWN_SECONDS")
    control_context_cache_prefix: str = Field(
        alias="CONTROL_CONTEXT_CACHE_PREFIX",
        min_length=1,
    )
    control_context_rebuild_lease_seconds: PositiveFloat = Field(
        alias="CONTROL_CONTEXT_REBUILD_LEASE_SECONDS"
    )
    control_context_rebuild_wait_seconds: PositiveFloat = Field(
        alias="CONTROL_CONTEXT_REBUILD_WAIT_SECONDS"
    )
    control_context_poll_interval_seconds: PositiveFloat = Field(
        alias="CONTROL_CONTEXT_POLL_INTERVAL_SECONDS"
    )
    investigation_retry_delays_seconds: list[PositiveFloat] = Field(
        alias="INVESTIGATION_RETRY_DELAYS_SECONDS"
    )
    record_comment_limit: PositiveInt = Field(alias="RECORD_COMMENT_LIMIT")
    analysis_model_call_limit: PositiveInt = Field(alias="ANALYSIS_MODEL_CALL_LIMIT")
    analysis_tool_call_limit: PositiveInt = Field(alias="ANALYSIS_TOOL_CALL_LIMIT")
    analysis_summarization_trigger_tokens: PositiveInt = Field(
        alias="ANALYSIS_SUMMARIZATION_TRIGGER_TOKENS"
    )
    analysis_summarization_keep_messages: PositiveInt = Field(
        alias="ANALYSIS_SUMMARIZATION_KEEP_MESSAGES"
    )
    sql_fixer_max_attempts: PositiveInt = Field(alias="SQL_FIXER_MAX_ATTEMPTS")
    summarizer_prompt_version: str = Field(alias="SUMMARIZER_PROMPT_VERSION", min_length=1)
    analysis_prompt_version: str = Field(alias="ANALYSIS_PROMPT_VERSION", min_length=1)
    comment_write_enabled: bool = Field(alias="COMMENT_WRITE_ENABLED")
    codes_write_enabled: bool = Field(alias="CODES_WRITE_ENABLED")

    @model_validator(mode="after")
    def validate_runtime_invariants(self) -> Self:
        if self.redis_iam_auth_enabled and not all(
            (self.redis_iam_user_id, self.redis_cache_name, self.redis_region)
        ):
            raise ValueError(
                "REDIS_IAM_USER_ID, REDIS_CACHE_NAME, and REDIS_REGION are required "
                "when REDIS_IAM_AUTH_ENABLED=true"
            )
        if self.ack_wait_seconds <= 2 * self.heartbeat_interval_seconds:
            raise ValueError("ACK_WAIT_SECONDS must exceed 2 × HEARTBEAT_INTERVAL_SECONDS")
        if self.lease_seconds <= self.ack_wait_seconds:
            raise ValueError("LEASE_SECONDS must exceed ACK_WAIT_SECONDS")
        if self.max_deliver <= self.max_attempts:
            raise ValueError("MAX_DELIVER must exceed MAX_ATTEMPTS")
        if not self.stable_threshold < self.decrease_threshold < self.break_threshold:
            raise ValueError("AIMD thresholds must satisfy stable < decrease < break")
        if not self.min_inflight <= self.initial_target <= self.max_per_instance:
            raise ValueError("INITIAL_TARGET must be between MIN_INFLIGHT and MAX_PER_INSTANCE")
        if self.increase_step_min > self.increase_step_max:
            raise ValueError("INCREASE_STEP_MIN must not exceed INCREASE_STEP_MAX")
        if self.control_context_rebuild_wait_seconds <= self.control_context_rebuild_lease_seconds:
            raise ValueError(
                "CONTROL_CONTEXT_REBUILD_WAIT_SECONDS must exceed "
                "CONTROL_CONTEXT_REBUILD_LEASE_SECONDS"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        environment_name = _environment_name(init_settings, env_settings, dotenv_settings)
        config_dir = _config_dir()
        required_files = [config_dir / "base.yaml", config_dir / f"{environment_name}.yaml"]
        missing_files = [path for path in required_files if not path.is_file()]
        if missing_files:
            missing = ", ".join(str(path) for path in missing_files)
            raise FileNotFoundError(f"Missing required config file(s): {missing}")
        candidates = [
            *required_files,
            config_dir / "services" / "worker.yaml",
            config_dir / "services" / f"worker.{environment_name}.yaml",
        ]
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(
                settings_cls,
                yaml_file=[path for path in candidates if path.is_file()],
                deep_merge=True,
            ),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
