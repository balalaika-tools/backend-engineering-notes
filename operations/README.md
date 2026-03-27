# Operations

> Testing and deployment patterns for Python backend applications.

[![pytest](https://img.shields.io/badge/pytest-8.x-0A9EDC.svg?logo=pytest&logoColor=white)](https://pytest.org)
[![Docker](https://img.shields.io/badge/Docker-latest-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Uvicorn](https://img.shields.io/badge/Uvicorn-0.30+-499848.svg)](https://www.uvicorn.org)
[![Gunicorn](https://img.shields.io/badge/Gunicorn-22.x-499848.svg)](https://gunicorn.org)

---

## Contents

| Section | Description |
|---------|-------------|
| [testing/](testing/README.md) | pytest, AsyncClient, dependency overrides, fixtures, mocking |
| [deployment/](deployment/README.md) | Docker, multi-stage builds, Uvicorn, Gunicorn, health checks, graceful shutdown |

---

## Prerequisites

- [fundamentals/fastapi/](../fundamentals/fastapi/README.md) — understand the application you're testing and deploying
- Basic pytest knowledge (for testing section)
- Basic Docker knowledge (for deployment section)
