# HTTPX Mental Model

> **Core idea**: Understand what happens at each layer when you make a request.

---

## 1. The Request Journey

A single request:

```python
await client.get("https://example.com")
```

travels through:

```
Coroutine
 → HTTPX client
 → Connection pool
 → Transport (asyncio / trio)
 → OS socket
 → TCP
 → TLS
 → HTTP/1.1 or HTTP/2
 → Network
```

HTTPX configuration controls:
- **How many of these steps may happen concurrently**
- **How long each step may block**

---

## 2. Sockets and Handshakes

### What Is a Socket?

A socket is an OS-managed TCP connection — a file descriptor the kernel uses to track network state.

### Creating a Socket Requires:

| Step | What happens | Time cost |
|------|--------------|-----------|
| 1. DNS resolution | Domain → IP address | Variable (cached vs uncached) |
| 2. TCP handshake | SYN → SYN-ACK → ACK | ~1 RTT |
| 3. TLS handshake | Certificate exchange, key derivation | ~1-2 RTT |

**This is expensive.** A typical handshake to a remote server takes 50-300ms.

### Reusing Sockets

Reusing an existing socket skips all three steps.

```
First request:  DNS + TCP + TLS + HTTP  (slow)
Second request: HTTP only               (fast)
```

This is why connection pooling exists.

---

## 3. The Connection Pool

HTTPX maintains a pool of TCP connections organized by:

```
(scheme, host, port)
```

### Without Pooling

```
Request 1 → new socket → DNS + TCP + TLS → close
Request 2 → new socket → DNS + TCP + TLS → close
Request 3 → new socket → DNS + TCP + TLS → close
```

Every request pays full handshake cost.

### With Pooling

```
Request 1 → new socket → DNS + TCP + TLS → keep open
Request 2 → reuse socket → HTTP only
Request 3 → reuse socket → HTTP only
```

Subsequent requests skip handshakes entirely.

### When Does a New Handshake Occur?

A new handshake happens only when:
- Server closes the connection (timeout, max requests)
- OS closes the connection (idle timeout)
- Pool limit forces closure (too many idle connections)
- Client explicitly closes the connection

---

## 4. The AsyncClient Lifecycle

### Creation

```python
client = httpx.AsyncClient(...)
```

Creates:
- Connection pool manager
- Transport layer
- Configuration state

**Does NOT** create any sockets yet.

### First Request

```python
await client.get("https://api.example.com/data")
```

Now:
- DNS lookup for `api.example.com`
- TCP connection established
- TLS handshake completed
- HTTP request sent
- Socket added to pool

### Subsequent Requests

```python
await client.get("https://api.example.com/other")
```

- Pool lookup: socket for `(https, api.example.com, 443)` exists
- Reuse socket
- HTTP request sent directly

### Cleanup

```python
await client.aclose()
```

- All pooled sockets closed
- Resources released

---

## 5. What Configuration Controls

HTTPX configuration affects different phases:

| Configuration | Phase | Effect |
|---------------|-------|--------|
| `limits.max_connections` | Pool | How many sockets can exist |
| `limits.max_keepalive_connections` | Pool | How many idle sockets to keep |
| `timeout.connect` | Handshake | DNS + TCP + TLS time budget |
| `timeout.pool` | Pool | Time to wait for free socket |
| `timeout.write` | Request | Time to send data |
| `timeout.read` | Response | Time to receive data |
| `http2=True` | Protocol | Enable multiplexing |

---

## 6. Concurrency Reality

### What Limits Concurrency?

The **number of available sockets** in the pool.

```python
limits = httpx.Limits(max_connections=10)
```

This means:
- At most 10 TCP connections exist simultaneously
- The 11th concurrent request **waits** for a socket

### What Happens When Pool Is Full?

```python
# 10 concurrent requests running
# 11th request arrives

await client.get(...)  # waits in pool queue
```

The request waits until:
1. A socket becomes free (request completes)
2. `pool` timeout expires (raises `PoolTimeout`)

---

## 7. Key Mental Model

```
┌─────────────────────────────────────────┐
│              Your Code                  │
│   await client.get("https://...")       │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│           HTTPX Client                  │
│   - Request building                    │
│   - Response parsing                    │
│   - Pool management                     │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│         Connection Pool                 │
│   - Socket lookup/creation              │
│   - Keep-alive management               │
│   - Concurrency enforcement             │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│           Transport                     │
│   - DNS resolution                      │
│   - TCP/TLS handshakes                  │
│   - Actual I/O                          │
└────────────────┬────────────────────────┘
                 │
                 ▼
            Network / Server
```

**Key insight**:

> Concurrency is limited at the **pool level**, not at the coroutine level.
> 100 coroutines with a pool of 10 connections = max 10 concurrent network operations.

---

## Summary

1. **Sockets are expensive to create** (DNS + TCP + TLS)
2. **Connection pooling amortizes this cost** across requests
3. **Pool limits control network concurrency**, not task concurrency
4. **Configuration affects different phases** of the request lifecycle
5. **The client manages the pool lifecycle** — create once, reuse, close properly

---

**Next**: [Connection Pooling](02_connection_pooling.md) — detailed pool configuration
