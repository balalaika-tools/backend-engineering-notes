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

## Contents

**Default one-note route:** read [01_mental_model.md](01_mental_model.md), make one request, and
print `response.http_version`. Stop there if you only need to understand the client → pool →
transport path. Continue to pooling or timeouts when load tests show queueing or stalled phases;
continue to advanced features or the comparison only for HTTP/2, streaming, or library selection.

| File | Topic | Read if you need to understand... |
|------|-------|-----------------------------------|
| [01_mental_model.md](01_mental_model.md) | Request lifecycle | What happens when you make a request |
| [02_connection_pooling.md](02_connection_pooling.md) | Pool limits | How connections are reused and limited |
| [03_timeouts.md](03_timeouts.md) | Phase-based timeouts | What each timeout controls |
| [04_advanced.md](04_advanced.md) | HTTP/2, streaming, errors | Advanced features and error handling |
| [05_httpx_vs_aiohttp.md](05_httpx_vs_aiohttp.md) | Library comparison | When to choose which |

---

## Quick Reference

### Configuration excerpt

Install HTTP/2 support with `pip install 'httpx[http2]'` before enabling `http2=True`. The excerpt
belongs in an application lifespan or `async with` block that closes the client at shutdown.

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

response = await client.get("https://example.com")
print(response.http_version)  # HTTP/2 when the server and dependency negotiate it
await client.aclose()
```

If this prints `HTTP/1.1`, the request still worked; either the optional HTTP/2 dependency is
missing or the server did not negotiate HTTP/2.

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
