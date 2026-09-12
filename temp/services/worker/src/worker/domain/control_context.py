"""Immutable control manuals, vocabularies, and schema context."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DecodeValue:
    code: str
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class DecodeSet:
    name: str
    description: str | None
    values: tuple[DecodeValue, ...]


@dataclass(frozen=True, slots=True)
class ControlManual:
    control_name: str
    config_md5: str
    content: str


@dataclass(frozen=True, slots=True)
class ControlContext:
    generation: int
    positions: ControlManual
    reference: ControlManual
    reason_codes: tuple[DecodeValue, ...]
    resolution_codes: tuple[DecodeValue, ...]
    positions_rec_schema: str
    positions_tenant_schema: str
    reference_rec_schema: str
    reference_tenant_schema: str
    reason_code_feature_id: str
    resolution_code_feature_id: str
