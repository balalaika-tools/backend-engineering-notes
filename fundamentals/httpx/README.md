# HTTPX — Practical Guide to Async HTTP

> Understand how HTTPX works at runtime before using it in production systems.

[![HTTPX](https://img.shields.io/badge/HTTPX-0.27+-009688.svg)](https://www.python-httpx.org)
[![HTTP/2](https://img.shields.io/badge/HTTP%2F2-supported-00A86B.svg)](https://httpwg.org/specs/rfc9113.html)

This guide explains HTTPX internals — **connection pooling**, **timeouts**, **HTTP/2**, and how it differs from aiohttp.

---

## Prerequisites

- Python async/await
- TCP/HTTP fundamentals
- What a socket is

---

## Guide Structure

| File | Topic | Read if you need to understand... |
|------|-------|-----------------------------------|
| [01_mental_model.md](01_mental_model.md) | Request lifecycle | What happens when you make a request |
| [02_connection_pooling.md](02_connection_pooling.md) | Pool limits | How connections are reused and limited |
| [03_timeouts.md](03_timeouts.md) | Phase-based timeouts | What each timeout controls |
| [04_advanced.md](04_advanced.md) | HTTP/2, streaming, errors | Advanced features and error handling |
| [05_httpx_vs_aiohttp.md](05_httpx_vs_aiohttp.md) | Library comparison | When to choose which |

---

## Quick Reference

### Minimal Production Client

```python
import httpx

client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_connections=50,
        max_keepalive_connections=10,
    ),
    timeout=httpx.Timeout(
        connect=5.0,
        pool=5.0,
        write=10.0,
        read=30.0,
    ),
    http2=True,
)
```

### Key Concepts

| Concept | What it controls |
|---------|------------------|
| `max_connections` | Peak concurrent sockets |
| `max_keepalive_connections` | Idle sockets retained |
| `connect` timeout | DNS + TCP + TLS handshake |
| `pool` timeout | Waiting for a free socket |
| `read` timeout | Response data reception |

---

## After This Guide

Once you understand HTTPX internals, proceed to:

**[Safe and Scalable API Calls](../fastapi/safe_and_scalable_api_calls/README.md)** — production-grade external API call patterns built on top of HTTPX.
