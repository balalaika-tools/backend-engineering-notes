# Caching and Performance

> **Who this is for**: API owners reducing latency and load without serving stale, private, or incorrectly varied data. Assumes [HTTP Semantics](02_http_semantics.md).

---

## 1. Caching Is Contract Behavior

An HTTP cache stores a response and may reuse it for a later request when the method, target, freshness, validators, and selection fields allow it.

```text
client → browser cache → CDN/shared cache → gateway cache → origin
                       cache hit ────────────────┘
```

Caching can remove network hops and origin work, but a wrong cache key can cross users or tenants. Design storage and reuse explicitly; do not rely on a platform's accidental defaults.

Two separate questions:

1. **May this response be stored?**
2. **May a stored response satisfy this request now?**

`no-cache` does not mean “do not store.” It means the response must be validated before reuse. `no-store` is the directive that asks caches not to store the message.

---

## 2. Private and Shared Caches

| Cache | Scope | Example |
|-------|-------|---------|
| Private | One user agent | Browser cache |
| Shared | Multiple consumers | CDN, reverse proxy |

Common response policies:

```http
# Public immutable asset or representation
Cache-Control: public, max-age=300

# Browser may store; shared cache must not
Cache-Control: private, max-age=60

# Store but validate on every reuse
Cache-Control: no-cache

# Sensitive response should not be stored
Cache-Control: no-store
```

Authenticated responses require special care. Do not place personalized content in a shared cache unless the cache key and directives deliberately separate every relevant identity/authorization variant. `private` or `no-store` is the safe default for sensitive per-user data.

---

## 3. Freshness and Validation

Fresh responses can be reused without contacting the origin. Stale responses normally need validation.

### Entity tag validation

```http
HTTP/1.1 200 OK
ETag: "order-17"
Cache-Control: private, max-age=0, must-revalidate
```

```http
GET /orders/ord_123
If-None-Match: "order-17"
```

```http
HTTP/1.1 304 Not Modified
ETag: "order-17"
```

The client reuses its stored body. A validator saves representation transfer and sometimes serialization, but the origin may still query storage to determine the current version.

### `Last-Modified`

Time validators are convenient but can lack sufficient resolution and may not capture every representation change. Prefer `ETag` when a reliable version or content validator exists.

Strong validators identify byte-equivalent representations. Weak tags such as `W/"..."` indicate semantic equivalence suitable for cache validation but not every range or concurrency use. Use strong tags for `If-Match` lost-update protection.

---

## 4. Cache Keys and `Vary`

At minimum, a cache key includes the request method and target URI. A response can add selection dimensions:

```http
Vary: Accept-Encoding, Accept-Language
```

If the body varies by `Origin` because CORS returns a specific allowed origin, the cache also needs `Vary: Origin`.

High-cardinality variation reduces hit rate and increases storage. `Vary: Authorization` is generally a warning that public shared caching may be the wrong design; prefer a consciously partitioned gateway cache or private caching.

Cache-key hazards:

- Ignoring a query parameter that changes representation
- Keying on a raw header that attackers can vary without bound
- Normalizing path/query differently at CDN and origin
- Caching responses involving cookies without deliberate policy
- Letting untrusted forwarding headers influence generated absolute URLs

Test the actual CDN/gateway configuration, not only origin headers.

---

## 5. Invalidation Strategies

| Strategy | Mechanism | Trade-off |
|----------|-----------|-----------|
| Short freshness | Small `max-age` | Predictable staleness; more origin traffic |
| Validation | `ETag` / `If-None-Match` | Origin round trip, smaller response/work |
| Purge | Invalidate cache key/tag on change | Fast freshness; provider-specific complexity |
| Versioned URI | New identifier when content changes | Excellent for immutable content; consumers need new URI |
| Event-driven refresh | Change event updates cache | Complex ordering and failure recovery |

Never claim strong read-after-write consistency through a cache unless invalidation and all relevant replicas provide it. A write response can return the new representation directly so the writer does not immediately depend on a stale shared read.

> **Principle**: Cache invalidation is a distributed state transition. It needs identity, ordering, observability, and a fallback TTL.

---

## 6. Performance Beyond Caching

Optimize after measuring end-to-end latency:

```text
DNS + connect + TLS + queue + application + database/dependencies
    + serialization + transfer + client processing
```

High-impact controls:

- Bound collection size and response bytes.
- Fix N+1 database/service access and add query indexes.
- Reuse client connections and tune pool limits.
- Apply realistic deadlines and admission control.
- Compress sufficiently large text responses; avoid spending CPU on tiny or already compressed bodies.
- Stream large results only when consumers can process incrementally and every proxy preserves streaming.
- Move long work to observable job resources.
- Precompute expensive shared representations where staleness permits.

HTTP/2 or HTTP/3 can improve connection use and multiplexing, but cannot repair slow queries or unlimited fan-out. Benchmark across the real gateway and client population.

---

## 7. Cache Security and Correctness Tests

Test at least:

- Two users request the same URI and never receive each other's body.
- Different language/encoding/origin variants do not cross.
- Authorization changes invalidate or bypass old cached decisions.
- `304` responses preserve the metadata required to reuse the stored response.
- Mutations invalidate every affected collection/member representation.
- Error responses have an intentional caching policy; `404` can be heuristically cacheable.
- Query parameter order and normalization agree across client, CDN, gateway, and application.
- Poisoned host/forwarding headers cannot enter cached links or redirects.

---

## References

- [HTTP Caching — RFC 9111](https://www.rfc-editor.org/rfc/rfc9111)
- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)

---

**Next**: [REST API Security](08_security.md)

