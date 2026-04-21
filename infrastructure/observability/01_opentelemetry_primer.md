# OpenTelemetry Primer

> **Who this is for**: Engineers new to OTel who want to understand what it actually is before touching code — what problems it solves, how the pieces relate, and why it has become the standard.

---

## 1. The Problem OTel Solves

Before OpenTelemetry, every monitoring vendor had its own SDK. If you used Datadog, you imported `ddtrace`. If you switched to Jaeger, you rewrote your instrumentation. If your team used Prometheus for metrics and Datadog for traces, you maintained two separate instrumentation models in the same codebase.

OpenTelemetry is a **vendor-neutral instrumentation standard**. You write your traces and metrics once against the OTel API. The backend you send them to becomes a configuration decision, not a code change.

> **Key insight**: OTel separates **instrumentation** (what you write in your app) from **export** (where the data goes). Change the backend without touching app code.

---

## 2. The Three Signals

OpenTelemetry models observability data as three distinct signal types. Each answers a different question.

| Signal | What It Answers | Example |
|---|---|---|
| **Traces** | What happened during this request? | A span tree showing DB calls, HTTP requests, queue publishes inside one `/checkout` request |
| **Metrics** | How is the system behaving over time? | `http_requests_total`, `db_pool_size`, `p99 latency` |
| **Logs** | What did the application say? | Structured log lines emitted during a request |

OTel supports all three. In practice, Python backends adopt them incrementally — traces first, then metrics, then logs. That rollout pattern is partly practical: traces and metrics are stable in OpenTelemetry Python, while the Python logs SDK is still marked `Development`/experimental, so many teams keep their existing logging pipeline and add OTel logs later.

---

## 3. Key Components

```
Your Python App
    │
    │  OTel SDK (opentelemetry-sdk)
    │  ┌─────────────────────────────────────────┐
    │  │  TracerProvider → spans                 │
    │  │  MeterProvider  → metrics               │
    │  │  LoggerProvider → log records           │
    │  └──────────────────────┬──────────────────┘
    │                         │  OTLP (gRPC or HTTP)
    ▼                         ▼
┌──────────────────────────────────────┐
│         OTel Collector               │
│  receivers → processors → exporters  │
└──────┬───────────────┬───────────────┘
       │               │
       ▼               ▼
  Groundcover       Prometheus
  (traces/logs)     (metrics)
```

### OTel SDK

The library you install in your app (`opentelemetry-sdk`, `opentelemetry-api`). It provides:

- **TracerProvider** — creates `Tracer` objects that produce spans
- **MeterProvider** — creates `Meter` objects that produce metric instruments
- **Exporters** — ship the data out (OTLP, Prometheus, stdout, etc.)
- **Processors** — batch, filter, or enrich before export

### OTLP

OTLP (OpenTelemetry Protocol) is the wire format OTel uses to send data between components. It runs over gRPC (port `4317`) or HTTP/protobuf (port `4318`). Both the SDK and the Collector speak OTLP natively.

> If a vendor says "we support OTel", they usually mean they accept OTLP ingestion.

### OTel Collector

A standalone process (binary or Docker container) that sits between your app and your backends. It receives OTLP from your app, applies transformations, and routes signals to one or more backends.

The Collector is **optional** — you can export directly from the SDK to a backend. But it becomes essential in production when you need routing, filtering, retries, or sending to multiple backends.

### Resource

A `Resource` is metadata attached to all telemetry from a single process. The most important attribute is `service.name`. Backends use it to group and filter signals by service.

```python
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

resource = Resource.create({SERVICE_NAME: "orders-api"})
```

Every `TracerProvider` and `MeterProvider` should receive the same `Resource`. Without it, backends cannot tell which service produced the data.

---

## 4. How Signals Flow

### Without a Collector (direct export)

```
App SDK  ──OTLP──▶  Backend (Groundcover, Jaeger, etc.)
         ──prom──▶  Prometheus scrapes /metrics endpoint
```

Simple. Works for dev and small setups. The app is responsible for knowing every backend address.

### With a Collector

```
App SDK  ──OTLP──▶  Collector  ──▶  Groundcover  (traces)
                               ──▶  Prometheus   (metrics via remote_write or scrape)
                               ──▶  S3           (logs archive)
```

The app only knows one address: the Collector. The Collector knows all the backends. Backend migrations, retries, and enrichment happen in Collector config, not app code.

---

## 5. Instrumentation Styles

### Manual instrumentation

You call OTel APIs directly in your code. Full control, explicit spans and metrics.

```python
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("process-order"):
    result = process(order)
```

### Auto-instrumentation

OTel provides instrumentation libraries for popular frameworks. They patch the framework at startup and emit spans automatically.

```bash
pip install opentelemetry-instrumentation-fastapi
pip install opentelemetry-instrumentation-sqlalchemy
pip install opentelemetry-instrumentation-httpx
```

These produce spans for every request, DB query, and outbound HTTP call without you writing a single `start_as_current_span`. See [02 — Python SDK Setup](02_python_sdk.md) for how to configure these in FastAPI.

---

## 6. Why OTel Over Vendor SDKs

| Concern | Vendor SDK | OpenTelemetry |
|---|---|---|
| Lock-in | Tied to one backend | Change backends by swapping exporter config |
| Multi-backend | Instrument twice | Instrument once, export to N backends via Collector |
| Standard | Per-vendor | CNCF standard; all major vendors accept OTLP |
| Auto-instrumentation | Vendor-specific | OTel contrib libraries cover FastAPI, SQLAlchemy, httpx, Redis, etc. |
| Traces + metrics + logs | Fragmented | One SDK for all three signals |

> **Key insight**: If you instrument with the Prometheus client directly for metrics and Datadog for traces, you now have two mental models. OTel is one model for all three signals, with backends chosen at deploy time.
