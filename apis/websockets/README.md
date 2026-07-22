# WebSocket Deep Dive

> Protocol and production architecture for persistent bidirectional application channels.

[![WebSocket](https://img.shields.io/badge/WebSocket-RFC_6455-005C9C.svg)](https://www.rfc-editor.org/rfc/rfc6455)
[![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/advanced/websockets/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_protocol_and_connection_lifecycle.md](01_protocol_and_connection_lifecycle.md) | Protocol lifecycle | Handshake, messages, frames, control frames, closure, and alternatives |
| [02_message_protocols_and_contracts.md](02_message_protocols_and_contracts.md) | Message contract | Envelopes, commands, events, correlation, validation, and evolution |
| [03_reliability_reconnection_and_flow_control.md](03_reliability_reconnection_and_flow_control.md) | Reliability | Heartbeats, reconnect, resume, ordering, acknowledgement, and backpressure |
| [04_authentication_and_security.md](04_authentication_and_security.md) | Security | Handshake auth, origin validation, authorization, limits, and revocation |
| [05_scaling_and_distributed_architecture.md](05_scaling_and_distributed_architecture.md) | Scaling | Connection ownership, brokers, fan-out, draining, and capacity |
| [06_implementation_testing_and_operations.md](06_implementation_testing_and_operations.md) | Implementation | FastAPI example, tests, metrics, deployment, and incident diagnosis |

---

## Reading Order

Read the protocol lifecycle first. The remaining files progress from the application contract to reliability, security, fleet architecture, and implementation.

---

## Prerequisites

- [API Fundamentals](../01_api_fundamentals.md)
- [FastAPI WebSockets](../../fundamentals/fastapi/06_websockets.md) for framework-specific patterns

