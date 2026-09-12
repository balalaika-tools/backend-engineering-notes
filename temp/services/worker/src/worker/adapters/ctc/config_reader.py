"""Read raw control configuration and decode sets from the CTC REST API."""

import base64
import binascii
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote

import httpx
from worker.adapters.ctc.client import CtcTransientError
from worker.domain.control_context import DecodeSet, DecodeValue
from worker.ports.control_context.control_config_source import (
    ControlConfigUnavailableError,
    ControlConfiguration,
    InvalidControlConfigError,
)


class CtcHttpClient(Protocol):
    async def request(self, method: str, path: str) -> httpx.Response: ...


class CtcConfigReader:
    def __init__(self, client: CtcHttpClient) -> None:
        self._client = client

    async def fetch_configuration(
        self,
        *,
        tenant_token: str,
        control_name: str,
    ) -> ControlConfiguration:
        configuration_path = (
            f"v1/tenants/{_segment(tenant_token)}/controls/{_segment(control_name)}/configuration"
        )
        response = await self._request(configuration_path)
        body = _json_object(response, resource="control configuration")
        encoded_control = body.get("control")
        expected_md5 = body.get("md5")
        if not isinstance(encoded_control, str) or not isinstance(expected_md5, str):
            raise InvalidControlConfigError("Control configuration must contain control and md5")
        try:
            xml = base64.b64decode(encoded_control, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise InvalidControlConfigError("Control configuration is not valid base64") from exc
        actual_md5 = hashlib.md5(xml, usedforsecurity=False).hexdigest()
        if actual_md5 != expected_md5.lower():
            raise InvalidControlConfigError("Control configuration md5 does not match its content")

        definition = _json_object(
            await self._request(f"{configuration_path}/control"),
            resource="control definition",
        )
        return ControlConfiguration(
            xml=xml,
            md5=actual_md5,
            rec_schema_name=_postgres_identifier(
                definition,
                "recSchemaName",
                resource="control definition",
            ),
            tenant_schema_name=_postgres_identifier(
                definition,
                "tenantSchemaName",
                resource="control definition",
            ),
            editable_feature_ids=_editable_feature_ids(xml),
        )

    async def fetch_decode_sets(self, *, tenant_token: str) -> tuple[DecodeSet, ...]:
        response = await self._request(f"v1/tenants/{_segment(tenant_token)}/decode-sets")
        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidControlConfigError("Decode sets response is not valid JSON") from exc
        if not isinstance(body, list):
            raise InvalidControlConfigError("Decode sets response must be a list")
        return tuple(_parse_decode_set(value) for value in body)

    async def _request(self, path: str) -> httpx.Response:
        try:
            response = await self._client.request("GET", path)
        except CtcTransientError as exc:
            raise ControlConfigUnavailableError(str(exc)) from exc
        if response.status_code in {401, 403, 429} or response.status_code >= 500:
            raise ControlConfigUnavailableError(
                f"CTC configuration request returned HTTP {response.status_code}"
            )
        if response.is_error:
            raise InvalidControlConfigError(
                f"CTC configuration request returned HTTP {response.status_code}"
            )
        return response


def _json_object(response: httpx.Response, *, resource: str) -> Mapping[str, object]:
    try:
        body = response.json()
    except ValueError as exc:
        raise InvalidControlConfigError(f"{resource.title()} response is not valid JSON") from exc
    if not isinstance(body, dict):
        raise InvalidControlConfigError(f"{resource.title()} response must be an object")
    return body


def _postgres_identifier(
    value: Mapping[str, object],
    key: str,
    *,
    resource: str,
) -> str:
    # CTC returns mixed-case logical names for PostgreSQL objects that were
    # created as unquoted identifiers, so PostgreSQL stores them in lowercase.
    return _required_string(value, key, resource=resource).lower()


def _parse_decode_set(value: object) -> DecodeSet:
    if not isinstance(value, dict):
        raise InvalidControlConfigError("Each decode set must be an object")
    name = _required_string(value, "name", resource="decode set")
    description = _optional_string(value, "description", resource=f"decode set {name}")
    decodes = value.get("decodes")
    if not isinstance(decodes, list):
        raise InvalidControlConfigError(f"Decode set {name} must contain a decodes list")
    return DecodeSet(
        name=name,
        description=description,
        values=tuple(_parse_decode(value, set_name=name) for value in decodes),
    )


def _parse_decode(value: object, *, set_name: str) -> DecodeValue:
    if not isinstance(value, dict):
        raise InvalidControlConfigError(f"Decode set {set_name} contains a non-object value")
    return DecodeValue(
        code=_required_string(value, "code", resource=f"decode set {set_name}"),
        name=_required_string(value, "name", resource=f"decode set {set_name}"),
        description=_optional_string(value, "description", resource=f"decode set {set_name}"),
    )


def _required_string(value: Mapping[str, object], key: str, *, resource: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise InvalidControlConfigError(f"{resource.title()} requires a non-empty {key}")
    return result


def _optional_string(value: Mapping[str, object], key: str, *, resource: str) -> str | None:
    result = value.get(key)
    if result is not None and not isinstance(result, str):
        raise InvalidControlConfigError(f"{resource.title()} {key} must be a string or null")
    return result


def _segment(value: str) -> str:
    return quote(value, safe="")


def _editable_feature_ids(xml: bytes) -> tuple[tuple[str, str], ...]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise InvalidControlConfigError("Control configuration is not valid XML") from exc
    features: list[tuple[str, str]] = []
    for element in root.iter():
        if element.attrib.get("isEditable") != "true":
            continue
        children = {_local_name(child.tag): (child.text or "").strip() for child in element}
        name = children.get("name")
        identifier = children.get("identifier")
        if name and identifier:
            features.append((name, identifier))
    return tuple(features)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
