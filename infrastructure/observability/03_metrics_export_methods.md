# Metrics Export Methods

> **Who this is for**: Engineers deciding how to wire metrics from a Python service into monitoring infrastructure — three ownership models, when to use each, and what the code actually looks like for each one.

---

## 1. The Core Question

When you instrument metrics in your Python app, two decisions need to be made independently:

1. **What API do you use to define metrics in app code?** (OTel metrics API, or Prometheus client directly)
2. **How does that data leave the app and reach the backend?** (Prometheus scrape, OTLP push, or both)

These two decisions produce three practical methods. The right one depends on what your org already operates.

---

## 2. Method Comparison

| | Method 1A | Method 1B | Method 2 |
|---|---|---|---|
| **Metrics API in app** | OTel | OTel | Prometheus client |
| **How metrics leave app** | Prometheus scrape (`/metrics`) | OTLP push to Collector | Prometheus scrape (`/metrics`) |
| **Tracing** | OTel → Groundcover | OTel → Collector → Groundcover | OTel → Groundcover |
| **Best for** | OTel metrics + existing Prometheus infra | Fully OTel-native, Collector-routed | Existing Prometheus-heavy org, OTel for traces only |
| **Metric instrumentation** | Single API | Single API | Two APIs (OTel traces + prom metrics) |
| **Operational complexity** | Medium | Higher (Collector required) | Low |
| **Risk of duplicate metrics** | High if not careful | Low | None |

---

## 3. Method 1A — OTel Metrics → Prometheus Scrape

Your app uses the OTel metrics API, but instead of pushing via OTLP, it exposes a `/metrics` endpoint that Prometheus scrapes. You get one instrumentation API (OTel) while staying compatible with existing Prometheus dashboards and alerting.

```
App (OTel traces + OTel metrics)
  ├── traces ──OTLP──▶ Groundcover
  └── metrics ──scrape──▶ Prometheus ──optional remote_write──▶ Groundcover
```

### App code

```python
import asyncio
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from prometheus_client import start_http_server

resource = Resource.create({SERVICE_NAME: os.getenv("SERVICE_NAME", "orders-api")})


def init_telemetry(app: FastAPI) -> None:
    # Traces → Groundcover via OTLP
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4317"),
                insecure=True,
            )
        )
    )
    trace.set_tracer_provider(trace_provider)

    # Metrics → PrometheusMetricReader (pull-based, responds to scrape requests)
    reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    FastAPIInstrumentor.instrument_app(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry(app)
    # Prometheus scrape port — separate from app port so it is not publicly exposed
    start_http_server(port=9464, addr="0.0.0.0")
    yield


app = FastAPI(lifespan=lifespan)

meter = metrics.get_meter(__name__)

request_counter = meter.create_counter(
    name="http_server_requests_total",
    description="Total HTTP requests",
)

request_duration = meter.create_histogram(
    name="http_server_request_duration_ms",
    description="Request latency",
    unit="ms",
)


@app.get("/checkout")
async def checkout():
    start = time.perf_counter()
    with trace.get_tracer(__name__).start_as_current_span("checkout"):
        await asyncio.sleep(0.05)
        request_counter.add(1, {"route": "/checkout", "method": "GET"})
        request_duration.record(
            (time.perf_counter() - start) * 1000,
            {"route": "/checkout", "method": "GET"},
        )
        return {"ok": True}
```

### How `PrometheusMetricReader` works

`PrometheusMetricReader` is a **pull exporter**. Unlike OTLP exporters which push on a timer, it holds metric data in memory and only serializes it when Prometheus makes an HTTP request. The `start_http_server(9464)` call from `prometheus_client` starts the HTTP server that answers those scrape requests.

> **Why a separate port (9464)?** The app typically runs on 8000 or 8080. Prometheus scrape targets are internal, not user-facing. Separating ports makes it easy to block 9464 at the load balancer while 8000 stays public. The OTel spec recommends port 9464 for Prometheus exporters.

### Important Prometheus caveats for Method 1A

- The Python OTel Prometheus exporter does **not** support multiprocessing. If you run multiple worker processes and each one tries to bind `9464`, you will get port conflicts or partial metrics. This method fits best when each process/pod exposes its own metrics endpoint.
- Resource attributes are not copied onto every metric series by default. Prometheus-style target metadata such as `service.name` is exposed via `job` / `instance` and a `target_info` metric, not as arbitrary labels on every time series.

### Prometheus scrape config

This goes in your Prometheus server configuration (`prometheus.yml`):

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "orders-api"
    scrape_interval: 15s          # how often Prometheus pulls from the app
    static_configs:
      - targets: ["orders-api:9464"]   # host:port of the app's metric server
    # Optional: only keep metrics you actually use
    metric_relabel_configs:
      - source_labels: [__name__]
        regex: "http_server.*|process.*"
        action: keep
```

In Kubernetes, use `kubernetes_sd_configs` with pod annotations instead of `static_configs`:

```yaml
scrape_configs:
  - job_name: "kubernetes-pods"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      # Only scrape pods that opt in with this annotation
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      # Combine the pod IP from __address__ with the port from the annotation
      - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        target_label: __address__
        regex: ([^:]+)(?::\d+)?;(\d+)
        replacement: "$1:$2"
```

Your pod manifest then opts in:

```yaml
# In your Deployment's pod template metadata
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9464"
```

### When to use Method 1A

- Your org has mature Prometheus dashboards and alerting you do not want to disrupt
- You want a single metrics API (OTel) in code
- You are adding traces now, and metrics are already being scraped

---

## 4. Method 1B — OTel Metrics → OTLP → Collector

Your app speaks only OTLP for both traces and metrics. A Collector receives all signals and routes them to different backends. The app has zero knowledge of where data ends up.

```
App (OTel traces + OTel metrics)
  └── OTLP ──▶ Collector
                 ├── traces ──▶ Groundcover
                 └── metrics ──▶ Prometheus remote_write / Victoria Metrics / etc.
```

### App code

```python
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

resource = Resource.create({SERVICE_NAME: os.getenv("SERVICE_NAME", "orders-api")})

COLLECTOR_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")


def init_telemetry(app: FastAPI) -> None:
    # Traces → Collector
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=COLLECTOR_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(trace_provider)

    # Metrics → Collector (pushed every 30s)
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=COLLECTOR_ENDPOINT, insecure=True),
        export_interval_millis=30_000,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    FastAPIInstrumentor.instrument_app(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry(app)
    yield


app = FastAPI(lifespan=lifespan)

meter = metrics.get_meter(__name__)

request_counter = meter.create_counter("http_server_requests_total", description="Total HTTP requests")
request_duration = meter.create_histogram("http_server_request_duration_ms", unit="ms")


@app.get("/checkout")
async def checkout():
    start = time.perf_counter()
    with trace.get_tracer(__name__).start_as_current_span("checkout"):
        request_counter.add(1, {"route": "/checkout", "method": "GET"})
        request_duration.record(
            (time.perf_counter() - start) * 1000,
            {"route": "/checkout", "method": "GET"},
        )
        return {"ok": True}
```

**Compared to 1A, the only changes are:**

- Removed `PrometheusMetricReader` and `start_http_server`
- Replaced with `PeriodicExportingMetricReader(OTLPMetricExporter(...))`
- Both traces and metrics go to the same `COLLECTOR_ENDPOINT`

For the Collector config that routes them to different backends, see [04 — OTel Collector Config](04_collector_config.md).

### When to use Method 1B

- You want the cleanest long-term setup
- You have a Collector already, or are willing to run one
- Your metric backend can accept OTLP or Prometheus Remote Write (VictoriaMetrics, Mimir, Grafana Cloud, etc.)
- You want backend routing to live in Collector config, not app code

---

## 5. Method 2 — OTel Traces Only, Prometheus-Native Metrics

Your app uses OTel only for distributed tracing. Metrics are written with the Prometheus Python client directly, not through OTel's API at all. This is the right choice when your org's metric ownership is firmly Prometheus-based and you are only adding tracing now.

```
App (OTel traces + prometheus_client metrics)
  ├── traces ──OTLP──▶ Groundcover
  └── metrics ──scrape──▶ Prometheus ──optional remote_write──▶ Groundcover
```

### App code

```python
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from prometheus_client import Counter, Histogram, start_http_server

resource = Resource.create({SERVICE_NAME: os.getenv("SERVICE_NAME", "orders-api")})


def init_telemetry(app: FastAPI) -> None:
    # OTel traces only
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4317"),
                insecure=True,
            )
        )
    )
    trace.set_tracer_provider(trace_provider)
    FastAPIInstrumentor.instrument_app(app)


# Prometheus-native instruments — defined at module level, not inside a function
REQUEST_COUNTER = Counter(
    "http_server_requests_total",
    "Total HTTP requests",
    ["route", "method"],
)

REQUEST_DURATION = Histogram(
    "http_server_request_duration_seconds",
    "Request latency in seconds",
    ["route", "method"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry(app)
    start_http_server(port=9464, addr="0.0.0.0")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/checkout")
async def checkout():
    start = time.perf_counter()
    with trace.get_tracer(__name__).start_as_current_span("checkout"):
        REQUEST_COUNTER.labels(route="/checkout", method="GET").inc()
        REQUEST_DURATION.labels(route="/checkout", method="GET").observe(
            time.perf_counter() - start
        )
        return {"ok": True}
```

**What changed compared to Method 1:**

- Removed all `opentelemetry.sdk.metrics` imports
- No `MeterProvider`, no `meter.create_counter(...)`, no OTel metric readers
- Added `from prometheus_client import Counter, Histogram, start_http_server`
- Metrics use `prometheus_client` API: `.labels(...).inc()` and `.labels(...).observe(...)`

### OTel metrics API vs Prometheus client API

| Action | OTel metrics API | Prometheus client API |
|---|---|---|
| Define a counter | `meter.create_counter("name")` | `Counter("name", "help", ["label1"])` |
| Increment | `counter.add(1, {"label": "val"})` | `counter.labels(label="val").inc()` |
| Record histogram | `histogram.record(val, {"label": "val"})` | `histogram.labels(label="val").observe(val)` |
| Export | Via reader (PrometheusMetricReader or OTLP) | Via `start_http_server` or WSGI app |

### Optional: forwarding Prometheus metrics into Groundcover

If your org already runs Prometheus and Groundcover accepts Prometheus Remote Write, you can forward metrics without touching app code:

```yaml
# In your prometheus.yml, add a remote_write block
remote_write:
  - url: "https://{BYOC_ENDPOINT}/api/v1/write"
    headers:
      apikey: "{your-groundcover-ingestion-key}"
      x-groundcover-service-name: "orders-api"
      x-groundcover-env-name: "production"
    queue_config:
      max_samples_per_send: 1000
      max_shards: 5
```

This is configured on the **Prometheus server**, not in app code. Groundcover documents this endpoint for non-Kubernetes (BYOC) environments. After ingestion, Prometheus-style metrics appear in Groundcover Explore and dashboards alongside your traces.

### When to use Method 2

- Prometheus dashboards and alerts are already mature and owned by another team
- You are only adding distributed tracing now — metrics are not in scope
- Lowest-risk rollout: zero changes to existing metric infrastructure

---

## 6. Avoiding Duplicate Metrics

The most common mistake in Method 1A is accidentally having the same metric in two places.

**This creates duplicates:**

```python
# In app code — OTel metric
meter.create_counter("http_requests_total")

# Also in app code — Prometheus native
Counter("http_requests_total", ...)
```

Both end up at the Prometheus scrape endpoint and both push to any OTLP backend. Dashboards then show doubled counts or conflicting label schemas.

**Rules to avoid this:**

- Pick one metrics API per service. Either OTel metrics or Prometheus client, not both.
- If using Method 1A (`PrometheusMetricReader`), never also import `prometheus_client` counters/histograms for the same logical metrics.
- In Method 2, if you later add OTel metrics, remove the equivalent Prometheus client instruments.
