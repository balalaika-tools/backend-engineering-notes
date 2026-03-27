# Background Work

> Running work outside the request-response cycle.

[![Dramatiq](https://img.shields.io/badge/Dramatiq-1.17+-7B4EA6.svg)](https://dramatiq.io)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.x-4B8BBE.svg)](https://apscheduler.readthedocs.io)
[![Airflow](https://img.shields.io/badge/Airflow-2.10+-017CEE.svg?logo=apacheairflow&logoColor=white)](https://airflow.apache.org)
[![Redis](https://img.shields.io/badge/Redis-broker-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)

This section answers the **"what tool should I use?"** question. For the **"how do I design the system?"** question (orchestration, worker patterns, client delivery), see [architecture/long_running_tasks](../architecture/long_running_tasks/).

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_overview.md](01_overview.md) | Task Execution Strategies | Dramatiq vs FastAPI BackgroundTasks vs APScheduler — when to use what |
| [02_dramatiq.md](02_dramatiq.md) | Dramatiq Deep Dive | Actors, brokers, retries, rate limits, pipelines, monitoring |
| [03_dramatiq_fastapi.md](03_dramatiq_fastapi.md) | Dramatiq + FastAPI | Integration patterns, project structure, testing, Docker Compose |
| [04_apscheduler.md](04_apscheduler.md) | APScheduler | Scheduled jobs, triggers, job stores, the multi-instance problem |
| [05_airflow.md](05_airflow.md) | Apache Airflow | DAGs, TaskFlow API, operators, sensors, dynamic mapping, Docker setup, production patterns |

---

## Quick Decision

| Need | Tool |
|------|------|
| Fire-and-forget after response (non-critical, <2s) | FastAPI `BackgroundTasks` |
| Reliable isolated execution, retries, heavy work | Dramatiq |
| Periodic/scheduled jobs (single instance) | APScheduler |
| Periodic jobs across multiple instances | APScheduler + Redis lock, or APScheduler triggering Dramatiq |
| Multi-step pipelines with dependencies, DAGs, UI | Airflow |

---

## Prerequisites

- [fundamentals/concurrency/](../fundamentals/concurrency/README.md) — understand async, threads, and processes
- Basic FastAPI knowledge (endpoints, dependency injection, lifespan)
- [infrastructure/redis/](../infrastructure/redis/README.md) — used as broker and result backend by Dramatiq
