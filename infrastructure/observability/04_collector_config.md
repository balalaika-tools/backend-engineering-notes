# OTel Collector Config

> **Who this is for**: Engineers deploying the OpenTelemetry Collector and writing its YAML config — understanding what every section does, why it exists, and how to route traces and metrics to different backends.

---

## 1. What the Collector Is and Why You Need It

The OTel Collector is a standalone binary (or Docker image) that receives telemetry from your apps, applies transformations, and forwards to backends. It is not part of your application code.

**Without a Collector:**

```
App → OTLP → Groundcover
App → Prometheus scrape endpoint (pull)
```

Your app has to know every backend address, retry logic, and export format.

**With a Collector:**

```
App → OTLP → Collector → Groundcover       (traces)
                       → VictoriaMetrics   (metrics)
                       → S3                (logs)
```

The app knows one address: the Collector. The Collector handles everything else.

### What the Collector gives you beyond "just forwarding"

| Capability | Without Collector | With Collector |
|---|---|---|
| Multi-backend routing | Instrument N exporters in app | One exporter in app, N in Collector config |
| Retry / backpressure | App must implement | Collector handles it |
| Filtering / dropping | App code change | Collector config change |
| Attribute enrichment | App code change | Collector processor config |
| Backend migration | Redeploy all apps | Redeploy Collector config |
| Tail sampling | Not possible from SDK | Possible in Collector |

---

## 2. Collector Config Anatomy

A practical Collector config usually centers on four top-level sections. Each section is independent; the `service` section wires them together. Current Collector configs can also include optional `connectors` and `extensions`, but you do not need those for a standard app-ingest pipeline.

```yaml
receivers:    # 1. How data gets IN to the Collector
processors:   # 2. What the Collector does WITH the data in-flight
exporters:    # 3. Where the data goes OUT
service:      # 4. Which receivers/processors/exporters are active and how they connect
```

> **Key insight**: Defining a receiver, processor, or exporter in the config does nothing on its own. It only takes effect when referenced in a `service.pipelines` block. This is a common source of confusion.

---

## 3. Receivers — How Data Gets In

Receivers listen for incoming telemetry.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317   # default gRPC port
      http:
        endpoint: 0.0.0.0:4318   # default HTTP/protobuf port
```

**Why both grpc and http?**

- gRPC is more efficient (binary, multiplexed, smaller wire format) — prefer it for app-to-Collector traffic
- HTTP/protobuf is useful for environments where gRPC is blocked (some proxies, service meshes)
- Most Python apps use gRPC by default (`OTLPSpanExporter` uses gRPC unless you import the HTTP variant)

Other common receivers:

```yaml
receivers:
  # Pull metrics from a Prometheus /metrics endpoint
  prometheus:
    config:
      scrape_configs:
        - job_name: "myapp"
          static_configs:
            - targets: ["myapp:9464"]

  # Receive logs from fluentd/fluentbit
  fluentforward:
    endpoint: 0.0.0.0:24224

  # Receive Jaeger-format traces (legacy migration)
  jaeger:
    protocols:
      grpc:
        endpoint: 0.0.0.0:14250
```

---

## 4. Processors — What Happens In-Flight

Processors sit between receivers and exporters. They transform, filter, or enrich data before it leaves the Collector.

### batch (always use this)

```yaml
processors:
  batch:
    timeout: 5s             # max time to wait before sending a batch
    send_batch_size: 1000   # max spans/metrics per batch
    send_batch_max_size: 2000
```

The `batch` processor groups data into batches before export. Without it, the Collector sends one span per export call, which is extremely inefficient and hammers backends with tiny requests. **Always include `batch` in every pipeline.**

### memory_limiter (always use this in production)

```yaml
processors:
  memory_limiter:
    check_interval: 5s
    limit_mib: 512          # hard limit — Collector starts dropping if exceeded
    spike_limit_mib: 128    # buffer for sudden spikes above the steady-state limit
```

If your app suddenly emits a spike of spans (traffic surge, runaway loop), the Collector's memory will grow unbounded without this. The `memory_limiter` drops data rather than OOMing. **Always put `memory_limiter` first in every pipeline.**

### resource (add/overwrite attributes)

```yaml
processors:
  resource:
    attributes:
      - key: deployment.environment.name
        value: "production"
        action: upsert          # insert if absent, overwrite if present
      - key: cloud.region
        value: "us-east-1"
        action: insert          # only insert if the key does not already exist
```

Use this to stamp attributes onto every span or metric from a particular Collector deployment — region, cluster, environment — without touching app code.

### attributes (add/drop/hash span-level attributes)

```yaml
processors:
  attributes:
    actions:
      - key: http.url
        action: delete          # remove a sensitive attribute from all spans
      - key: user.email
        action: hash            # hash PII rather than dropping it
      - key: team
        value: "platform"
        action: insert
```

`resource` operates on `Resource` attributes (service-level). `attributes` operates on span/metric-level attributes. They are different scopes.

> **Attribute names depend on the app's semconv setting.** The `http.url` key above is the *old* HTTP semconv name. If your services opt into the stable HTTP conventions (`OTEL_SEMCONV_STABILITY_OPT_IN=http`, see [02](02_python_sdk.md)), that attribute is instead `url.full` (with `url.path` / `url.query`), and `http.method` / `http.status_code` become `http.request.method` / `http.response.status_code`. Match your processor rules to whatever the apps actually emit, or they will silently no-op.

### filter (drop entire spans or metrics)

```yaml
processors:
  filter:
    error_mode: ignore   # ignore | silent | propagate (default). Use ignore: log OTTL errors and continue.
    traces:
      span:
        # Drop health check spans entirely
        - 'attributes["http.route"] == "/health"'
        - 'attributes["http.route"] == "/metrics"'
```

> Without `error_mode: ignore`, any OTTL expression evaluation error causes the Collector to drop the span and propagate an error. For drop rules, `ignore` is almost always the right choice.

---

## 5. Exporters — Where Data Goes Out

Exporters send data to backends.

### OTLP exporter (to any OTLP-accepting backend)

```yaml
exporters:
  otlp/groundcover:                        # /groundcover is an alias — required when you have multiple OTLP exporters
    endpoint: "ingest.groundcover.com:443"
    headers:
      Authorization: "Bearer ${env:GROUNDCOVER_API_KEY}"   # env var substitution
    tls:
      insecure: false

  otlp/jaeger:
    endpoint: "jaeger:4317"
    tls:
      insecure: true
```

> The `/name` suffix (e.g., `otlp/groundcover`) is how you define **multiple instances** of the same exporter type. Without it, you can only have one OTLP exporter. With it, you can fan out the same signal to multiple backends.

### Prometheus Remote Write exporter

```yaml
exporters:
  prometheusremotewrite:
    endpoint: "https://victoria-metrics.internal/api/v1/write"
    headers:
      X-Scope-OrgID: "myorg"
    tls:
      insecure_skip_verify: false
    resource_to_telemetry_conversion:
      enabled: true   # converts Resource attributes into Prometheus labels
```

`resource_to_telemetry_conversion.enabled` is powerful but easy to overuse. Its default is `false`. Turning it on copies **all** resource attributes into metric labels, which is convenient for querying but can create expensive cardinality if attributes such as pod IDs, container IDs, or instance IDs vary frequently.

### Prometheus exporter (expose a scrape endpoint from the Collector)

```yaml
exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"   # Prometheus scrapes this port on the Collector
    namespace: "otelcol"
    resource_to_telemetry_conversion:
      enabled: true
```

This makes the Collector a Prometheus scrape target. Useful when your metric backend can only pull (i.e., does not accept Remote Write).

By default, Prometheus exporters do **not** copy every resource attribute onto every metric. Instead, Prometheus target identity is carried through `job` / `instance`, and the remaining resource attributes are typically exposed via a `target_info` metric. Promote extra resource attributes to labels only when you actually need them for queries.

### Debug exporter (debug only)

```yaml
exporters:
  debug:
    verbosity: detailed   # normal | basic | detailed — dev/debug only, never production
```

> The `logging` exporter was removed in Collector v0.111.0. Use `debug` instead. `loglevel` is replaced by `verbosity`.

---

## 6. Service — Wiring It All Together

The `service` section declares which components are active and how they connect into pipelines.

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/groundcover]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/groundcover]
```

**Rules:**

- Each pipeline is independent. Traces, metrics, and logs can have completely different receivers, processors, and exporters.
- Processors run in the order listed. Put `memory_limiter` first to protect against OOM before any enrichment.
- A component not referenced in `service.pipelines` is defined but never runs.

---

## 7. Full Production Config

This config matches **Method 1B** from [03 — Metrics Export Methods](03_metrics_export_methods.md): the app sends all signals via OTLP, the Collector routes traces to Groundcover and metrics to a Prometheus-compatible backend.

```yaml
# otel-collector-config.yaml

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 5s
    limit_mib: 512
    spike_limit_mib: 128

  batch:
    timeout: 5s
    send_batch_size: 1000

  resource:
    attributes:
      - key: deployment.environment.name
        value: ${env:ENVIRONMENT}      # injected via environment variable
        action: upsert

  filter/drop_health:
    error_mode: ignore
    traces:
      span:
        - 'attributes["http.route"] == "/health"'
        - 'attributes["http.route"] == "/readiness"'

exporters:
  # Traces → Groundcover
  otlp/groundcover:
    endpoint: "ingest.groundcover.com:443"
    headers:
      Authorization: "Bearer ${env:GROUNDCOVER_API_KEY}"

  # Metrics → VictoriaMetrics (or any Prometheus-compatible backend)
  prometheusremotewrite:
    endpoint: "https://victoria-metrics.internal/api/v1/write"
    resource_to_telemetry_conversion:
      enabled: true

  # Logs → Groundcover (same OTLP endpoint, different pipeline)
  otlp/groundcover_logs:
    endpoint: "ingest.groundcover.com:443"
    headers:
      Authorization: "Bearer ${env:GROUNDCOVER_API_KEY}"

service:
  telemetry:
    logs:
      level: WARN                # Collector's own log level — not your app's logs
    metrics:
      readers:
        - pull:
            exporter:
              prometheus:
                host: 0.0.0.0
                port: 8888

  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, filter/drop_health, resource, batch]
      exporters: [otlp/groundcover]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [prometheusremotewrite]

    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [otlp/groundcover_logs]
```

### Processor order in the traces pipeline

```
memory_limiter → filter/drop_health → resource → batch → exporter
```

1. **memory_limiter first** — drop data if memory is tight, before doing any work on it
2. **filter** — drop health check spans before enriching them (no point enriching what you discard)
3. **resource** — stamp environment attributes onto spans
4. **batch** — group into efficient export batches
5. **exporter** — send

---

## 8. Where the Collector Runs

### Docker Compose (local dev / simple deployments)

```yaml
# docker-compose.yml

services:
  app:
    build: .
    environment:
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4317"
      SERVICE_NAME: "orders-api"
    depends_on:
      - otel-collector

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.150.0   # current example version as of 2026-04-21
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
      - "8888:8888"   # Collector self-metrics (Prometheus scrape)
    environment:
      GROUNDCOVER_API_KEY: "${GROUNDCOVER_API_KEY}"
      ENVIRONMENT: "development"
```

> **`otel/opentelemetry-collector` vs `otel/opentelemetry-collector-contrib`**: `prometheusremotewrite` is available in current core and contrib distributions, but `-contrib` remains the safer default when you want broad component availability. Pin a recent Collector version instead of copying stale image tags from old examples.

### Kubernetes (DaemonSet — one Collector per node)

```yaml
# collector-daemonset.yaml

apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: otel-collector
  namespace: monitoring
spec:
  selector:
    matchLabels:
      app: otel-collector
  template:
    metadata:
      labels:
        app: otel-collector
    spec:
      containers:
        - name: otel-collector
          image: otel/opentelemetry-collector-contrib:0.150.0
          args: ["--config=/conf/config.yaml"]
          ports:
            - containerPort: 4317    # OTLP gRPC
            - containerPort: 4318    # OTLP HTTP
            - containerPort: 8888    # self-metrics
          volumeMounts:
            - name: config
              mountPath: /conf
          env:
            - name: GROUNDCOVER_API_KEY
              valueFrom:
                secretKeyRef:
                  name: otel-secrets
                  key: groundcover-api-key
            - name: ENVIRONMENT
              value: "production"
          resources:
            limits:
              memory: 512Mi
              cpu: 500m
            requests:
              memory: 128Mi
              cpu: 100m
      volumes:
        - name: config
          configMap:
            name: otel-collector-config
```

Apps on the same node send to `http://$(NODE_IP):4317`. Each app's pod spec injects the node IP:

```yaml
env:
  - name: NODE_IP
    valueFrom:
      fieldRef:
        fieldPath: status.hostIP
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://$(NODE_IP):4317"
```

**DaemonSet vs Deployment for the Collector:**

| Mode | When to Use |
|---|---|
| DaemonSet | Apps send to the Collector on the same node — no cross-node network hop, scales automatically with cluster |
| Deployment | Single central Collector — simpler config, useful for tail sampling which needs to see all spans from a trace |
| Sidecar | Collector runs per pod — maximum isolation, high resource overhead at scale |

---

## 9. Environment Variable Substitution in Config

The Collector supports environment-variable substitution throughout the YAML. The documented syntax is `${env:VAR_NAME}`. This is how you avoid hardcoding secrets and environment-specific values.

```yaml
exporters:
  otlp/groundcover:
    endpoint: "${env:GROUNDCOVER_ENDPOINT}"
    headers:
      Authorization: "Bearer ${env:GROUNDCOVER_API_KEY}"
```

Pass the variables via your container's environment:

```bash
docker run \
  -e GROUNDCOVER_ENDPOINT="ingest.groundcover.com:443" \
  -e GROUNDCOVER_API_KEY="abc123" \
  -v ./config.yaml:/etc/otelcol-contrib/config.yaml \
  otel/opentelemetry-collector-contrib:0.150.0
```

In Kubernetes, use `secretKeyRef` for secrets and `configMapKeyRef` for non-sensitive values, as shown in the DaemonSet example above.

> **Version-sensitive note**: `service.telemetry.metrics.address` was deprecated and, as of Collector `v0.123.0`, ignored. Use `service.telemetry.metrics.readers` for Collector self-metrics in current configs.
