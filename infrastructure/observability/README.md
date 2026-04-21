# Observability & OpenTelemetry

> **Who this is for**: Backend engineers instrumenting Python services for production — understanding what OpenTelemetry is, how to integrate it with Prometheus and monitoring platforms like Groundcover, and how to configure the OTel Collector.

![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000?style=flat&logo=opentelemetry)
![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python)

---

## Contents

| File | What It Covers |
|---|---|
| [01 — OpenTelemetry Primer](01_opentelemetry_primer.md) | What OTel is, the three signals, SDK vs Collector, OTLP, why OTel over vendor SDKs |
| [02 — Python SDK Setup](02_python_sdk.md) | TracerProvider, MeterProvider, instruments, auto-instrumentation, FastAPI integration |
| [03 — Metrics Export Methods](03_metrics_export_methods.md) | Three ownership models: OTel → Prometheus scrape, OTel → OTLP Collector, Prometheus-native with OTel traces only |
| [04 — OTel Collector Config](04_collector_config.md) | Collector YAML anatomy, receivers / processors / exporters / pipelines explained, where it runs and why |

---

## Prerequisites

- [Structured Logging Guide](../../fundamentals/core_concepts/structlog_guide.md) — understand structured log context before adding trace IDs
- [FastAPI Patterns](../../fundamentals/fastapi/) — middleware and lifespan hooks used in instrumentation setup
- [Docker & Deployment](../../operations/deployment/docker_and_deployment.md) — where the Collector runs alongside your app
