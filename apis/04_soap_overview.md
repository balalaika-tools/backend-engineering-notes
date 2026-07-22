# SOAP: A Focused Overview

> **Who this is for**: Engineers integrating with an existing SOAP service or evaluating a requirement for WSDL and WS-* standards. Assumes [API Styles and Selection](02_api_styles_and_selection.md).

---

## 1️⃣ The Mental Model

**SOAP** is an XML-based messaging protocol. A SOAP message is an envelope that can carry application data in its body and extensible processing instructions in headers.

```text
SOAP service description
WSDL ──references──> XML Schema types
  │
  ├── operations and messages
  ├── protocol binding
  └── service address

generated client ──SOAP Envelope──> service
                 <──SOAP Envelope──
```

SOAP is not merely “an XML REST API.” Its processing model defines envelopes, header roles, faults, intermediary behavior, and transport bindings. HTTP is the usual transport, but SOAP's messaging framework is transport-independent.

SOAP 1.1 and SOAP 1.2 differ in namespaces, media types, and some HTTP binding details. Follow the service's WSDL and generated client configuration; do not mix versions by copying headers from an unrelated example.

---

## 2️⃣ Message Anatomy

```xml
<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope
    xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
    xmlns:pay="https://payments.example.com/schema">
  <soap:Header>
    <pay:RequestContext soap:mustUnderstand="true">
      <pay:RequestId>req_01J...</pay:RequestId>
    </pay:RequestContext>
  </soap:Header>
  <soap:Body>
    <pay:GetPayment>
      <pay:PaymentId>pay_123</pay:PaymentId>
    </pay:GetPayment>
  </soap:Body>
</soap:Envelope>
```

| Part | Purpose |
|------|---------|
| `Envelope` | Required document root and SOAP version namespace |
| `Header` | Optional processing metadata such as security, addressing, or transactions |
| `Body` | Application request, response, or fault |
| `Fault` | Standard error structure carried in the body |

`mustUnderstand="true"` tells a targeted SOAP node that it must process the header or fail. This is stronger than attaching an arbitrary HTTP header and hoping every intermediary understands it.

---

## 3️⃣ WSDL and XML Schema

A **WSDL** describes the callable interface; **XML Schema (XSD)** describes the data types. Tooling can generate client proxies and server skeletons from them.

| WSDL concept | Meaning |
|--------------|---------|
| Interface/port type | Operations exposed by the service |
| Message/input/output | Data exchanged for an operation |
| Binding | How abstract operations map to SOAP/HTTP details |
| Service/endpoint | Concrete network address |

Generated clients are normally safer than hand-building XML because they apply namespaces, types, bindings, and fault mappings consistently. Still inspect the generated request and enable wire logging in a redacted test environment when diagnosing interoperability.

---

## 4️⃣ Faults and HTTP Status

A SOAP fault carries structured failure information:

```xml
<soap:Fault xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Code>
    <soap:Value>soap:Sender</soap:Value>
  </soap:Code>
  <soap:Reason>
    <soap:Text xml:lang="en">Payment ID is malformed</soap:Text>
  </soap:Reason>
  <soap:Detail>
    <errorCode xmlns="https://payments.example.com/schema">INVALID_ID</errorCode>
  </soap:Detail>
</soap:Fault>
```

Consumers must handle both transport failure and SOAP faults. Do not treat every HTTP response containing XML as success, and do not retry a `Sender`/client fault as if it were a transient network failure.

---

## 5️⃣ Why SOAP Still Exists

SOAP's wider ecosystem includes specifications commonly called **WS-***: WS-Security, WS-Addressing, reliable messaging, policies, and transaction-related standards. These can meet formal enterprise interoperability requirements that would otherwise require custom conventions.

Use SOAP when:

- An existing provider exposes only a SOAP contract.
- A regulated or enterprise integration mandates a WSDL or particular WS-* profile.
- Mature vendor tooling and formal XML types matter more than payload simplicity.

Avoid choosing it by default for a greenfield web or mobile API when ordinary HTTPS, OAuth, JSON, and OpenAPI satisfy the actual requirements. WS-* capability is valuable only when every participating implementation supports the same profiles correctly.

---

## 6️⃣ Integration Checklist

- Generate from the exact WSDL revision and pin generated artifacts.
- Confirm SOAP version, binding, endpoint, TLS, proxy, and client-certificate requirements.
- Validate clock synchronization when message-level timestamps or signatures are used.
- Set connection and response timeouts; generated clients often have unsafe defaults.
- Redact credentials and sensitive XML elements from wire logs.
- Limit XML size and depth; disable unsafe external entity resolution.
- Map SOAP faults into a stable internal error taxonomy.
- Test namespaces, optional/nillable fields, decimal and date handling, and large responses.
- Contract-test against a sandbox and capture representative fault fixtures.

---

## References

- [SOAP Version 1.2 Part 1: Messaging Framework](https://www.w3.org/TR/soap12-part1/)
- [SOAP Version 1.2 Part 2: Adjuncts](https://www.w3.org/TR/soap12-part2/)
- [WSDL Version 2.0 Primer](https://www.w3.org/TR/wsdl20-primer/)

---

**Next**: [GraphQL Overview](05_graphql_overview.md)

