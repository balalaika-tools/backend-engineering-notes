"""CTC control configuration response mapping."""

import base64
import hashlib

import httpx
import pytest
from worker.adapters.ctc.client import CtcTransientError
from worker.adapters.ctc.config_reader import CtcConfigReader
from worker.ports.control_context.control_config_source import (
    ControlConfigUnavailableError,
    InvalidControlConfigError,
)


class FakeClient:
    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, path: str) -> httpx.Response:
        self.requests.append((method, path))
        return self.responses[path]


@pytest.mark.asyncio
async def test_decodes_documented_configuration_response_and_preserves_md5() -> None:
    xml = b"""<rec xmlns="http://www.greshamtech.com/ctc/rec">
      <groupFeature decodeSet="ReasonCodes" isEditable="true">
        <identifier>7099e75b-9ffc-4a89-b0f6-631b56a821e1</identifier>
        <name>ExceptionReasonCode</name>
      </groupFeature>
      <groupFeature decodeSet="ResolutionCodes" isEditable="true">
        <identifier>1774b347-716f-4fe1-90f1-5602c2234f3a</identifier>
        <name>ExceptionResolutionCode</name>
      </groupFeature>
    </rec>"""
    digest = hashlib.md5(xml, usedforsecurity=False).hexdigest()
    path = "v1/tenants/WEBUI/controls/Positions/configuration"
    definition_path = f"{path}/control"
    client = FakeClient(
        {
            path: httpx.Response(
                200,
                json={"control": base64.b64encode(xml).decode(), "md5": digest},
            ),
            definition_path: httpx.Response(
                200,
                json={
                    "recSchemaName": "webuiPositions",
                    "tenantSchemaName": "webui",
                },
            ),
        }
    )

    configuration = await CtcConfigReader(client).fetch_configuration(
        tenant_token="WEBUI",
        control_name="Positions",
    )

    assert configuration.xml == xml
    assert configuration.md5 == digest
    assert configuration.rec_schema_name == "webuipositions"
    assert configuration.tenant_schema_name == "webui"
    assert dict(configuration.editable_feature_ids) == {
        "ExceptionReasonCode": "7099e75b-9ffc-4a89-b0f6-631b56a821e1",
        "ExceptionResolutionCode": "1774b347-716f-4fe1-90f1-5602c2234f3a",
    }
    assert client.requests == [("GET", path), ("GET", definition_path)]


@pytest.mark.asyncio
async def test_parses_decode_sets_in_documented_order() -> None:
    path = "v1/tenants/WEBUI/decode-sets"
    client = FakeClient(
        {
            path: httpx.Response(
                200,
                json=[
                    {
                        "name": "ReasonCodes",
                        "description": "Why the break occurred",
                        "fqn": "WEBUI.ReasonCodes",
                        "decodes": [
                            {
                                "code": "CA Event",
                                "name": "Corporate action event",
                                "description": "A corporate action caused the break",
                                "fqn": "WEBUI.ReasonCodes.CA Event",
                            },
                            {
                                "code": "IBOR Claim",
                                "name": "IBOR claim",
                                "description": None,
                                "fqn": "WEBUI.ReasonCodes.IBOR Claim",
                            },
                        ],
                    }
                ],
            )
        }
    )

    decode_sets = await CtcConfigReader(client).fetch_decode_sets(tenant_token="WEBUI")

    assert decode_sets[0].name == "ReasonCodes"
    assert [value.code for value in decode_sets[0].values] == ["CA Event", "IBOR Claim"]
    assert decode_sets[0].values[0].description == "A corporate action caused the break"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
async def test_auth_and_temporary_responses_are_transient(status_code: int) -> None:
    path = "v1/tenants/WEBUI/decode-sets"
    reader = CtcConfigReader(FakeClient({path: httpx.Response(status_code)}))

    with pytest.raises(ControlConfigUnavailableError):
        await reader.fetch_decode_sets(tenant_token="WEBUI")


@pytest.mark.asyncio
async def test_transport_failure_from_authenticated_client_is_transient() -> None:
    class FailingClient:
        async def request(self, method: str, path: str) -> httpx.Response:
            raise CtcTransientError("connection failed")

    with pytest.raises(ControlConfigUnavailableError):
        await CtcConfigReader(FailingClient()).fetch_decode_sets(tenant_token="WEBUI")


@pytest.mark.asyncio
async def test_rejects_configuration_when_md5_does_not_match() -> None:
    path = "v1/tenants/WEBUI/controls/Positions/configuration"
    client = FakeClient(
        {
            path: httpx.Response(
                200,
                json={
                    "control": base64.b64encode(b"<rec />").decode(),
                    "md5": "00000000000000000000000000000000",
                },
            )
        }
    )

    with pytest.raises(InvalidControlConfigError, match="md5"):
        await CtcConfigReader(client).fetch_configuration(
            tenant_token="WEBUI",
            control_name="Positions",
        )


@pytest.mark.asyncio
async def test_escapes_tenant_and_control_as_single_path_segments() -> None:
    path = "v1/tenants/tenant%2Fone/controls/control%20one/configuration"
    definition_path = f"{path}/control"
    xml = b"<rec />"
    client = FakeClient(
        {
            path: httpx.Response(
                200,
                json={
                    "control": base64.b64encode(xml).decode(),
                    "md5": hashlib.md5(xml, usedforsecurity=False).hexdigest(),
                },
            ),
            definition_path: httpx.Response(
                200,
                json={"recSchemaName": "tenantControl", "tenantSchemaName": "tenant"},
            ),
        }
    )

    await CtcConfigReader(client).fetch_configuration(
        tenant_token="tenant/one",
        control_name="control one",
    )

    assert client.requests == [("GET", path), ("GET", definition_path)]


@pytest.mark.asyncio
async def test_rejects_control_definition_without_schema_names() -> None:
    path = "v1/tenants/WEBUI/controls/Positions/configuration"
    xml = b"<rec />"
    reader = CtcConfigReader(
        FakeClient(
            {
                path: httpx.Response(
                    200,
                    json={
                        "control": base64.b64encode(xml).decode(),
                        "md5": hashlib.md5(xml, usedforsecurity=False).hexdigest(),
                    },
                ),
                f"{path}/control": httpx.Response(
                    200,
                    json={"recSchemaName": "webuiPositions"},
                ),
            }
        )
    )

    with pytest.raises(InvalidControlConfigError, match="tenantSchemaName"):
        await reader.fetch_configuration(
            tenant_token="WEBUI",
            control_name="Positions",
        )
