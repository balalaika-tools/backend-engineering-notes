# Documentation Backlog

Living backlog for future documentation in this repository.

Last reviewed: 2026-07-01

This file is intentionally a planning document, not a promise to build everything. The goal is to keep future additions focused on the gaps that matter most for production Python backend work: security, release safety, operations, data correctness, and AI/API-heavy systems.

## Priority Key

| Priority | Meaning |
|----------|---------|
| P0 | Add next. Missing knowledge can lead to production incidents, security bugs, or unsafe releases. |
| P1 | High-value addition. The repo is usable without it, but it would noticeably improve the learning path. |
| P2 | Useful expansion. Add when the nearby section is being worked on. |
| P3 | Parking lot. Good idea, but not urgent or not yet clearly scoped. |

## Recommended Build Order

1. `fundamentals/fastapi/11_api_security.md`
2. `operations/deployment/02_ci_cd_and_releases.md`
3. `infrastructure/messaging/`
4. `infrastructure/observability/05_alerting_and_slos.md`
5. `fundamentals/database/07_query_performance.md`
6. `architecture/llm_systems/`

## P0 - Add Next

### `fundamentals/fastapi/11_api_security.md`

Status: Not started

Why: The repo now covers authentication, JWTs, OAuth, CORS, and API design, but production API security is broader than "who is this user?" The missing layer is "what can this caller do, what can they abuse, and what should the system refuse by default?"

Should cover:

- Authentication vs authorization vs auditability.
- Object-level authorization: owner checks, tenant checks, and IDOR/BOLA prevention.
- Function-level authorization: admin routes, role checks, capability checks, and deny-by-default dependencies.
- Object property authorization: avoiding over-posting, mass assignment, and accidental response leaks.
- CORS, CSRF, cookies, bearer tokens, and browser trust boundaries.
- Rate limits, quotas, cost controls, and business-flow abuse.
- SSRF defenses for endpoints that fetch URLs or call webhooks.
- Webhook verification: signatures, timestamps, replay windows, idempotency keys.
- File upload safety: size limits, content-type distrust, scanning hooks, object storage, signed URLs.
- Secrets handling: environment variables, secret managers, logging redaction, rotation.
- Safe error responses: do not leak stack traces, SQL, token claims, or provider internals.
- Third-party API consumption: validate upstream data like untrusted input.
- Security observability: audit logs, sensitive events, anomaly signals, and alert examples.
- A final FastAPI checklist for new endpoints.

Best sources to align with:

- OWASP API Security Top 10 2023: https://owasp.org/API-Security/editions/2023/en/0x11-t10/
- NIST Secure Software Development Framework: https://csrc.nist.gov/Projects/ssdf
- OAuth 2.0 Security Best Current Practice, RFC 9700: https://www.rfc-editor.org/rfc/rfc9700.html

### `operations/deployment/02_ci_cd_and_releases.md`

Status: Not started

Why: The deployment docs explain Docker, Uvicorn, Gunicorn, health checks, and graceful shutdown. The missing production layer is release engineering: how code safely moves from commit to production.

Should cover:

- CI vs CD vs release management.
- Pipeline stages: format, lint, type check, unit tests, integration tests, migration checks, image build, security scan, deploy, smoke test.
- GitHub Actions workflow anatomy for a Python/FastAPI service.
- Deployment environments, protected environments, approvals, and environment-specific secrets.
- OIDC/workload identity instead of long-lived cloud credentials where possible.
- Image tagging: commit SHA, semantic version, environment tags, and immutability.
- Database migration release patterns: expand/contract, backward-compatible migrations, lock awareness, rollback limits.
- Blue-green, canary, rolling deploys, feature flags, and kill switches.
- Post-deploy verification: smoke tests, synthetic checks, SLO watch windows.
- Rollback playbooks: app rollback, migration rollback, config rollback, data repair.
- Release checklist and "do not deploy if" gates.

Best sources to align with:

- GitHub Actions deployment environments: https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments
- NIST SSDF: https://csrc.nist.gov/Projects/ssdf
- Kubernetes probes: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

## P1 - High-Value Additions

### `infrastructure/messaging/README.md`

Status: Not started

Why: Messaging is currently discussed inside long-running task architecture, but brokers deserve their own infrastructure section. A backend engineer should understand why Redis Streams, RabbitMQ, SQS, Kafka, and PostgreSQL `LISTEN/NOTIFY` are not interchangeable.

Suggested files:

- `infrastructure/messaging/README.md`
- `infrastructure/messaging/01_broker_decision_guide.md`
- `infrastructure/messaging/02_rabbitmq.md`
- `infrastructure/messaging/03_sqs.md`
- `infrastructure/messaging/04_kafka.md`
- `infrastructure/messaging/05_message_contracts_and_outbox.md`

Should cover:

- Queue vs pub/sub vs log vs stream.
- Broker selection by workload: task queue, event bus, audit log, fanout, ordered processing.
- Delivery semantics: at-most-once, at-least-once, effectively-once, exactly-once claims.
- Consumer acknowledgements, visibility timeouts, prefetch, backpressure, and flow control.
- Retries, exponential backoff, poison messages, dead-letter queues, and parking lots.
- Idempotent consumers and deduplication keys.
- Message contracts: schema versioning, compatibility, event envelopes, correlation IDs.
- Transactional outbox and inbox patterns.
- Operational concerns: lag, queue depth, consumer health, retention, replay, and cost.

Best sources to align with:

- RabbitMQ consumer acknowledgements and publisher confirms: https://www.rabbitmq.com/docs/confirms
- RabbitMQ dead-letter exchanges: https://www.rabbitmq.com/docs/dlx
- Amazon SQS developer guide: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/welcome.html
- Apache Kafka documentation: https://kafka.apache.org/documentation/

### `infrastructure/observability/05_alerting_and_slos.md`

Status: Not started

Why: The current observability section explains OpenTelemetry setup and export paths. The missing operational lesson is how to decide what matters, when to alert, and how to avoid creating noisy dashboards nobody trusts.

Should cover:

- Observability vs monitoring vs alerting.
- SLIs, SLOs, SLAs, error budgets, and burn-rate alerts.
- RED metrics for request/response services: rate, errors, duration.
- USE metrics for infrastructure: utilization, saturation, errors.
- What should page a human vs create a ticket.
- Alert anti-patterns: CPU-only pages, alerting on symptoms without user impact, duplicate pages.
- Dashboard design: service overview, dependency view, deploy view, incident view.
- Trace-driven debugging and log correlation.
- Runbook links, ownership, severity levels, and escalation.
- Example FastAPI service SLO and Prometheus-style alert rules.

Best sources to align with:

- Google SRE Service Level Objectives: https://sre.google/sre-book/service-level-objectives/
- OpenTelemetry observability primer: https://opentelemetry.io/docs/concepts/observability-primer/

### `fundamentals/database/07_query_performance.md`

Status: Not started

Why: The database section covers drivers, ORM, pooling, async sessions, and migrations. It does not yet teach the day-two skill of making slow queries understandable and safe to improve.

Should cover:

- How to read `EXPLAIN` and `EXPLAIN ANALYZE`.
- Sequential scans vs index scans vs bitmap scans.
- Index basics: B-tree, composite indexes, partial indexes, expression indexes.
- Query shape: selectivity, sort, join order, limits, offsets, keyset pagination.
- N+1 queries in SQLAlchemy and how loader strategies affect query count.
- Locking and transaction duration as performance issues.
- Slow query logging and `pg_stat_statements`.
- When to denormalize, cache, precompute, or add a read model.
- Benchmark hygiene: production-like data volume, cold vs warm cache, noise.
- A checklist before adding an index.

Best sources to align with:

- PostgreSQL `EXPLAIN`: https://www.postgresql.org/docs/current/using-explain.html
- PostgreSQL indexes: https://www.postgresql.org/docs/current/indexes.html

### `architecture/llm_systems/README.md`

Status: Not started

Why: The repo says it is focused on AI and API-heavy projects, and there is now an LLM testing guide. A dedicated architecture section would connect prompts, provider APIs, structured outputs, retrieval, evals, cost controls, and safety into one coherent production story.

Suggested files:

- `architecture/llm_systems/README.md`
- `architecture/llm_systems/01_provider_abstraction.md`
- `architecture/llm_systems/02_prompt_and_schema_design.md`
- `architecture/llm_systems/03_retrieval_augmented_generation.md`
- `architecture/llm_systems/04_evals_and_regression.md`
- `architecture/llm_systems/05_safety_cost_and_observability.md`

Should cover:

- Provider adapter boundaries and model-specific capability flags.
- Structured outputs, schema validation, refusal handling, and retries.
- Prompt versioning and prompt inputs as data.
- Tool/function calling boundaries and least-privilege tools.
- RAG ingestion, chunking, metadata, citations, freshness, and deletion.
- Evals: golden sets, adversarial cases, judge models, human review, and drift.
- Cost controls: token accounting, budget gates, caching, batching, fallbacks.
- Safety: prompt injection, insecure output handling, sensitive data disclosure, excessive agency.
- Observability: traces per LLM call, token usage, latency, model version, eval failures.

Best sources to align with:

- OpenAI structured outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI evals: https://developers.openai.com/api/docs/guides/evals
- Anthropic evaluation tool: https://platform.claude.com/docs/en/test-and-evaluate/eval-tool
- OWASP Top 10 for LLM Applications: https://owasp.org/www-project-top-10-for-large-language-model-applications/

## P2 - Useful Expansions

### `operations/testing/14_load_and_performance_testing.md`

Status: Not started

Should cover:

- Load testing vs stress testing vs soak testing vs spike testing.
- Tools such as Locust, k6, hey, wrk, and pytest-benchmark.
- Testing FastAPI endpoints with realistic concurrency and payloads.
- Coordinating app, database, Redis, and external API limits.
- Reading latency percentiles: p50, p95, p99, max, coordinated omission.
- How to avoid testing only localhost fantasy conditions.
- Regression thresholds in CI and when not to run load tests in CI.

### `operations/incident_response/README.md`

Status: Not started

Suggested files:

- `operations/incident_response/README.md`
- `operations/incident_response/01_on_call_and_severity.md`
- `operations/incident_response/02_runbooks.md`
- `operations/incident_response/03_postmortems.md`

Should cover:

- Incident severity levels.
- Triage loop: detect, declare, mitigate, communicate, resolve, review.
- Runbook structure and examples.
- Postmortems without blame.
- What to capture during an incident: timeline, decisions, metrics, deploys, dependencies.

### `operations/deployment/03_kubernetes_runtime.md`

Status: Not started

Should cover:

- Deployment, Service, Ingress, ConfigMap, Secret, Job, CronJob.
- Requests and limits.
- Readiness, liveness, and startup probes.
- Rolling updates and pod disruption budgets.
- Graceful shutdown for ASGI apps and workers.
- Horizontal Pod Autoscaling and the metrics it should use.
- Config and secret injection.
- Common FastAPI-on-Kubernetes failure modes.

### `fundamentals/database/08_data_modeling_and_constraints.md`

Status: Not started

Should cover:

- Constraints as business logic: primary key, foreign key, unique, check, exclusion.
- Normalization vs denormalization.
- Soft deletes and archival.
- Temporal data and audit tables.
- Multi-column uniqueness and partial uniqueness.
- Modeling state machines in SQL.
- JSONB tradeoffs.

### `fundamentals/database/09_multi_tenancy.md`

Status: Not started

Should cover:

- Tenant isolation models: shared schema, separate schema, separate database.
- Tenant IDs and every-query filtering.
- PostgreSQL row-level security.
- Noisy-neighbor risks.
- Per-tenant migrations, backups, exports, and deletes.
- Tenant-aware caching and rate limits.

### `fundamentals/fastapi/12_file_uploads_and_object_storage.md`

Status: Not started

Should cover:

- Upload size limits and streaming uploads.
- Temporary files and memory pressure.
- MIME type distrust and content sniffing.
- Virus scanning hooks.
- S3-compatible object storage.
- Presigned URLs.
- Download authorization.
- Metadata tables and object lifecycle policies.

### `fundamentals/fastapi/13_webhooks_and_integrations.md`

Status: Not started

Should cover:

- Receiving webhooks safely.
- Signature verification and timestamp windows.
- Idempotency, retries, and replay handling.
- Async processing of webhook payloads.
- Outbound webhooks: signing, retry schedule, DLQ, customer-visible delivery logs.
- Testing webhooks locally and in CI.

### `infrastructure/secrets/README.md`

Status: Not started

Should cover:

- Configuration vs secrets.
- `.env` files for local development only.
- Secret managers: AWS Secrets Manager, SSM Parameter Store, Vault, Doppler, 1Password.
- Rotation, versioning, lease duration, and emergency revocation.
- Secret scanning in CI.
- Avoiding secret leakage in logs, traces, exceptions, and screenshots.

### `architecture/event_driven_architecture/README.md`

Status: Not started

Should cover:

- Events vs commands.
- Domain events vs integration events.
- Event schema design and versioning.
- Outbox/inbox patterns.
- Choreography vs orchestration.
- Eventual consistency and user experience.
- Event replay and projection rebuilds.

### `architecture/resilience/README.md`

Status: Not started

Should cover:

- Timeouts, retries, circuit breakers, bulkheads, load shedding, and backpressure as one system.
- Failure budgets and retry budgets.
- Regional failure and dependency failure.
- Graceful degradation.
- Cell-based architecture and blast-radius reduction.
- When resilience patterns make things worse.

## P3 - Parking Lot

These are worthwhile, but they are less urgent than the production safety gaps above.

- `fundamentals/python_packaging/README.md` - packaging, dependency pinning, wheels, build backends, lockfiles.
- `fundamentals/data_validation/README.md` - deeper Pydantic, schemas as contracts, compatibility.
- `infrastructure/search/README.md` - PostgreSQL full-text search, OpenSearch, vector search tradeoffs.
- `infrastructure/object_storage/README.md` - S3-compatible storage as its own infrastructure topic if file uploads grow.
- `operations/cost_management/README.md` - cloud cost, LLM token cost, Redis/Postgres sizing, tagging.
- `architecture/multi_region/README.md` - active/passive, active/active, data replication, failover testing.
- `architecture/saas_patterns/README.md` - tenant lifecycle, plans/billing hooks, entitlements, quotas.
- `operations/developer_experience/README.md` - local dev, Makefiles, pre-commit, dev containers, seed data.

## Already Covered Enough For Now

These topics do not need a new document immediately. Extend existing docs only when there is a concrete gap.

| Topic | Where it already lives |
|-------|------------------------|
| Basic FastAPI request handling | `fundamentals/fastapi/01_http_and_parameter_mapping.md` |
| Dependency injection | `fundamentals/fastapi/02_dependency_injection.md` |
| Pydantic basics | `fundamentals/fastapi/03_pydantic.md` |
| JWT and OAuth fundamentals | `fundamentals/auth/` and `fundamentals/fastapi/04_authentication.md` |
| Error response shape | `fundamentals/fastapi/07_error_handling.md` |
| Streaming and SSE | `fundamentals/fastapi/08_streaming.md` and safe/scalable streaming docs |
| Background tasks | `fundamentals/fastapi/09_background_tasks_and_routers.md` and `background_work/` |
| Async SQLAlchemy setup | `fundamentals/database/04_async_sqlalchemy.md` |
| Connection pooling | `fundamentals/database/05_connection_pooling.md` and `fundamentals/httpx/02_connection_pooling.md` |
| Alembic migrations | `fundamentals/database/06_alembic.md` |
| Redis basics and patterns | `infrastructure/redis/` |
| OpenTelemetry setup | `infrastructure/observability/` |
| LLM code testing | `operations/testing/13_testing_llm_code.md` |

## Maintenance Rules

- When a backlog item is implemented, either remove it or move it to "Already Covered Enough For Now."
- Prefer one strong chapter over many thin chapters.
- Before creating a new section, check whether the topic belongs in an existing guide.
- For security, infrastructure, and LLM topics, re-check primary sources before writing because guidance changes.
- Each new doc should include: mental model, decision guide, production pitfalls, code examples, test strategy, and source links.
