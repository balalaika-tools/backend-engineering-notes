"""HTTP contracts for configuration administration."""

from pydantic import BaseModel, ConfigDict


class ResetConfigurationRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{}, {"purge_manuals": True}]})

    purge_manuals: bool = False


class ResetConfigurationResponse(BaseModel):
    generation: int
