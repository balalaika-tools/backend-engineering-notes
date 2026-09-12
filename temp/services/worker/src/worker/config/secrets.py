"""Worker secrets loaded from environment variables or `.env`."""

from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """Credential material, masked in representations and validation output."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    platform_db_password: SecretStr = Field(alias="PLATFORM_DB_PASSWORD")
    ctc_db_password: SecretStr = Field(alias="CTC_DB_PASSWORD")
    ctc_client_secret: SecretStr = Field(alias="CTC_CLIENT_SECRET")
    nats_auth_token: SecretStr = Field(alias="NATS_AUTH_TOKEN")
    s3_access_key_id: SecretStr | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: SecretStr | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")

    @model_validator(mode="after")
    def validate_s3_credentials(self) -> Self:
        if (self.s3_access_key_id is None) != (self.s3_secret_access_key is None):
            raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together")
        return self


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()  # type: ignore[call-arg]
