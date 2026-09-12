"""Typed, non-secret orchestrator configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    ByteSize,
    Field,
    PositiveFloat,
    PositiveInt,
    field_validator,
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
        "max_investigation_batch_size",
        "max_report_batch_size",
        "report_inline_limit",
        "platform_pool_max_size",
        "ctc_pool_max_size",
        "ctc_acquisition_timeout_seconds",
        "ctc_statement_timeout_seconds",
        "ctc_scan_page_size",
        "max_bulk_exceptions",
        "outbox_poll_interval_seconds",
        "outbox_batch_size",
        "database_retention_days",
        "manual_key_prefix",
        "nats_stream",
        "nats_subject",
        "jwks_cache_ttl_seconds",
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
    override = os.environ.get("ORCHESTRATOR_CONFIG_DIR")
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
    """Non-secret settings resolved before constructing runtime resources."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment_name: EnvironmentName = Field(
        alias="ENVIRONMENT_NAME",
        description="Deployment environment selecting the YAML policy baseline.",
    )
    platform_database_url: AnyUrl = Field(
        alias="PLATFORM_DATABASE_URL",
        description="Credential-free async URL for the platform PostgreSQL database.",
    )
    ctc_database_url: AnyUrl = Field(
        alias="CTC_DATABASE_URL", description="Credential-free CTC PostgreSQL URL."
    )
    ctc_reconciliation_schema: str = Field(
        alias="CTC_RECONCILIATION_SCHEMA", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    ctc_pool_max_size: PositiveInt = Field(alias="CTC_POOL_MAX_SIZE")
    ctc_acquisition_timeout_seconds: PositiveFloat = Field(alias="CTC_ACQUISITION_TIMEOUT_SECONDS")
    ctc_statement_timeout_seconds: PositiveFloat = Field(alias="CTC_STATEMENT_TIMEOUT_SECONDS")
    ctc_scan_page_size: PositiveInt = Field(alias="CTC_SCAN_PAGE_SIZE")
    max_bulk_exceptions: PositiveInt = Field(alias="MAX_BULK_EXCEPTIONS")

    @field_validator("ctc_database_url")
    @classmethod
    def validate_ctc_url(cls, value: AnyUrl) -> AnyUrl:
        if (
            value.scheme != "postgresql+asyncpg"
            or value.password is not None
            or (
                value.query is not None
                and value.query not in {"ssl=require", "ssl=verify-ca", "ssl=verify-full"}
            )
        ):
            raise ValueError(
                "CTC_DATABASE_URL must be an asyncpg URL without a password; only a TLS ssl query option is allowed"
            )
        if not value.host or not value.username or not value.path or value.path == "/":
            raise ValueError("CTC_DATABASE_URL requires a host, reader username, and database")
        return value

    nats_url: AnyUrl = Field(alias="NATS_URL", description="NATS server URL.")
    s3_bucket: str = Field(alias="S3_BUCKET", min_length=1, description="Object-store bucket.")
    cognito_issuer: AnyHttpUrl = Field(
        alias="COGNITO_ISSUER",
        description="JWT issuer whose JWKS signs access tokens.",
    )
    cognito_audience: str = Field(
        alias="COGNITO_AUDIENCE",
        min_length=1,
        description="Allowed Cognito access-token client identifier.",
    )
    s3_endpoint: AnyHttpUrl | None = Field(
        default=None,
        alias="S3_ENDPOINT",
        description="Optional S3-compatible endpoint for local development.",
    )
    otlp_endpoint: AnyHttpUrl | None = Field(
        default=None,
        alias="OTLP_ENDPOINT",
        description="Optional OTLP collector endpoint; telemetry is disabled when absent.",
    )
    service_instance_id: str | None = Field(
        default=None,
        alias="SERVICE_INSTANCE_ID",
        description="Optional runtime instance identity.",
    )
    service_namespace: str = Field("exception-investigation", alias="SERVICE_NAMESPACE")
    service_version: str = Field("unknown", alias="SERVICE_VERSION")
    log_full_exception_trace: bool = Field(True, alias="LOG_FULL_EXCEPTION_TRACE")
    otel_export_interval_millis: PositiveInt = Field(15_000, alias="OTEL_EXPORT_INTERVAL_MILLIS")

    log_level: LogLevel = Field(alias="LOG_LEVEL", description="Structured logging level.")
    tenant_token: str = Field(alias="TENANT_TOKEN", min_length=1, description="CTC tenant token.")
    positions_control: str = Field(
        alias="POSITIONS_CONTROL", min_length=1, description="Positions control name."
    )
    reference_control: str = Field(
        alias="REFERENCE_CONTROL", min_length=1, description="Reference control name."
    )
    exception_name: str = Field(
        alias="EXCEPTION_NAME", min_length=1, description="CTC exception type name."
    )
    max_investigation_batch_size: PositiveInt = Field(
        alias="MAX_INVESTIGATION_BATCH_SIZE",
        description="Maximum trigger and status lookup batch size.",
    )
    max_report_batch_size: PositiveInt = Field(
        alias="MAX_REPORT_BATCH_SIZE", description="Maximum reports batch size."
    )
    report_inline_limit: ByteSize = Field(
        alias="REPORT_INLINE_LIMIT", description="Largest report retained inline."
    )
    platform_pool_max_size: PositiveInt = Field(
        alias="PLATFORM_POOL_MAX_SIZE", description="Maximum platform database pool size."
    )
    outbox_poll_interval_seconds: PositiveFloat = Field(
        alias="OUTBOX_POLL_INTERVAL_SECONDS", description="Publisher polling interval."
    )
    outbox_batch_size: PositiveInt = Field(
        alias="OUTBOX_BATCH_SIZE", description="Maximum rows claimed by one publisher poll."
    )
    database_retention_days: PositiveInt = Field(
        alias="DATABASE_RETENTION_DAYS",
        description="Historical platform database retention in days.",
    )
    manual_key_prefix: str = Field(
        alias="MANUAL_KEY_PREFIX", min_length=1, description="Object key prefix for manuals."
    )
    nats_stream: str = Field(alias="NATS_STREAM", min_length=1, description="JetStream name.")
    nats_subject: str = Field(
        alias="NATS_SUBJECT", min_length=1, description="Investigation event subject."
    )
    jwks_cache_ttl_seconds: PositiveInt = Field(
        alias="JWKS_CACHE_TTL_SECONDS", description="JWKS cache lifetime."
    )

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
            config_dir / "services" / "orchestrator.yaml",
            config_dir / "services" / f"orchestrator.{environment_name}.yaml",
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
