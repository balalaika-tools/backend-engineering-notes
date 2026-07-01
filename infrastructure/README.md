# Infrastructure & Tools

> External services and tools your application depends on.

[![Redis](https://img.shields.io/badge/Redis-7.x-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker-latest-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000?style=flat&logo=opentelemetry)](https://opentelemetry.io)
[![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus)](https://prometheus.io)

---

## Contents

| Section | Description |
|---------|-------------|
| [redis/](redis/README.md) | Data structures, pub/sub, streams, caching patterns, Python clients |
| [observability/](observability/README.md) | OpenTelemetry primer, Python SDK setup, metrics export methods, OTel Collector config |
| [Messaging options](../architecture/long_running_tasks/04_infrastructure.md#2-rabbitmq-amqp) | RabbitMQ, SQS, Kafka, and when Redis is not enough |

---

## Prerequisites

- [fundamentals/concurrency/](../fundamentals/concurrency/README.md) — async/await before using async Redis clients
- [fundamentals/fastapi/](../fundamentals/fastapi/README.md) — dependency injection patterns for client setup
