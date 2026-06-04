# Redis HA and Persistence

> A single Redis node is a single point of failure and a single point of data loss. This guide covers the three things you have to think about for production Redis: **persistence** (what survives a crash), **high availability** (what happens on node failure), and **scale** (what happens when one node is not enough). It also covers the failure modes you actually need to plan for.

---

## 1. Persistence — What Survives a Restart

Redis is an **in-memory** store. Without persistence enabled, a `SHUTDOWN` or OOM kill loses everything. Redis ships two persistence mechanisms, not mutually exclusive.

### RDB — Snapshots

Periodic full-state snapshots written to disk as a binary file (`dump.rdb`).

```
save 3600 1    # snapshot if ≥1 key changed in 3600s
save 300 100   # snapshot if ≥100 keys changed in 300s
save 60 10000  # snapshot if ≥10000 keys changed in 60s
```

**Pros:**
- Compact file, fast to restore on boot.
- Snapshot is `fork()`-based — the child writes while the parent keeps serving. Low impact on request latency in steady state.
- Good for point-in-time backups (copy the RDB somewhere off-node).

**Cons:**
- Data loss window is the time since the last snapshot — minutes, typically. A crash between snapshots loses everything written in that window.
- The `fork()` can be slow on a multi-GB instance (copies page tables); transient latency spike.

### AOF — Append-Only File

Every write command is appended to a log. On startup, Redis replays the log.

```
appendonly yes
appendfsync everysec     # most common setting
# appendfsync always     # fsync every write — durable but slow
# appendfsync no         # let the OS decide — fast but up to ~30s loss
```

**Pros:**
- Much smaller loss window: with `everysec`, at most ~1s of writes lost on crash. With `always`, none (but every write blocks on fsync, which is slow).
- Human-readable log — easier to inspect / surgically repair.

**Cons:**
- Larger on disk than RDB.
- AOF files grow forever unless rewritten. Redis does an auto-rewrite when the file exceeds a threshold (`auto-aof-rewrite-percentage 100`, `auto-aof-rewrite-min-size 64mb`) — this fork()s and writes a new compact AOF in the background.
- Replay on restart is slower than loading an RDB (though modern Redis writes both RDB and AOF into a hybrid file for faster replay).

### Recommended combo

For production: **enable both.**

```
save 3600 1
save 300 100
appendonly yes
appendfsync everysec
aof-use-rdb-preamble yes   # hybrid: RDB preamble + AOF tail
```

- On restart, Redis uses the AOF (better durability). The hybrid preamble means the first bulk of state loads at RDB speed, not AOF replay speed.
- RDB snapshots still happen for off-node backup.
- Durability: at most ~1s of writes lost on an OS crash. Zero data loss requires `appendfsync always` (slow) and replication (below).

### "Nothing" is also a valid choice

If you use Redis purely as a **cache** — the source of truth is Postgres, every Redis key is derivable, and a cold restart just means "one query is slow until the cache warms" — persistence is useless overhead. Turn both off: `save ""`, `appendonly no`. The explicit empty-string `save ""` is required to disable RDB; the default is on.

---

## 2. Replication — The Building Block for HA

A replica keeps a continuous copy of a primary. Configured with:

```
replicaof primary-host 6379
```

Replication is **asynchronous** by default: the primary acks the write to the client as soon as it applies locally, then forwards the command to the replica. This means:

- **Read-after-write on a replica can lag** by milliseconds to seconds.
- A primary crash between "ack the client" and "replicate to replica" loses those last few writes. There is no synchronous replication in Redis; the `WAIT` command blocks on replication but does not make the ack itself synchronous.

Replicas are read-only by default. Use them for read scaling (reporting, cache reads) with an understanding that reads may be slightly stale.

Replication alone is **not** HA — it gives you a warm copy but no automatic failover. For that you need Sentinel or Cluster.

---

## 3. Redis Sentinel — Automatic Failover for Single-Key-Space Deployments

Sentinel is a separate process (usually 3 or 5 sentinels) that watches one primary and its replicas, detects failure by consensus, and promotes a replica.

```
                            ┌───────────┐
                            │ Sentinel1 │
          ┌─────────────────┤ Sentinel2 │
          │                 │ Sentinel3 │
          │                 └───────────┘
          ▼
    ┌─────────────┐          ┌───────────┐
    │   Primary   │─────────▶│ Replica 1 │
    │ (write + read)          └───────────┘
    └─────────────┘          ┌───────────┐
                    ────────▶│ Replica 2 │
                             └───────────┘
```

### What Sentinel does

1. Pings the primary and replicas on an interval.
2. When enough sentinels (the "quorum") agree the primary is unreachable, they elect a leader sentinel.
3. The leader picks a replica to promote (preferring the most up-to-date one).
4. Existing replicas are reconfigured to replicate from the new primary.
5. Clients ask sentinels for the current primary address on each reconnect — so they learn about the new primary without manual intervention.

### What Sentinel does not

- **Shard data.** One Sentinel cluster manages one primary's worth of data. If you need more RAM than a single node, you need Cluster.
- **Prevent data loss.** Asynchronous replication means the replica you promote may be behind the primary by the window between primary failure and replica replay. `min-replicas-to-write` and `min-replicas-max-lag` are partial mitigations — the primary refuses writes if fewer than N replicas are in sync.

### Sensible defaults

- **3 sentinels minimum**, deployed on separate failure domains from each other and from the Redis nodes. Two is insufficient (can't reach quorum after one fails).
- **One writable primary + 1–2 replicas.** Clients use the `Sentinel` API to discover the primary.
- **`min-replicas-to-write 1`**: if no replica is connected, the primary refuses writes — prefers unavailability over split-brain data loss.

### Python client

```python
from redis.sentinel import Sentinel

sentinel = Sentinel([
    ("sentinel-1", 26379),
    ("sentinel-2", 26379),
    ("sentinel-3", 26379),
], socket_timeout=0.5)

primary = sentinel.master_for("mymaster", socket_timeout=0.5)
replica = sentinel.slave_for("mymaster", socket_timeout=0.5)

primary.set("k", "v")
value = replica.get("k")   # may be stale by replication lag
```

---

## 4. Redis Cluster — Sharding + HA

Cluster does two things Sentinel does not:

1. **Horizontal sharding.** The keyspace is split into **16384 hash slots**; each master owns a subset. Clients hash the key (`CRC16(key) mod 16384`) to find the owning master.
2. **Per-shard HA.** Each shard has its own primary + replicas; the cluster fails over each shard independently.

```
Cluster:
  shard 0: slots 0-5460      master A, replica A'
  shard 1: slots 5461-10922  master B, replica B'
  shard 2: slots 10923-16383 master C, replica C'
```

### When Cluster vs Sentinel

| You need | Use |
|----------|-----|
| More RAM than a single node, or writes exceeding one core | Cluster |
| HA but your data fits on one node | Sentinel (simpler, fewer moving parts) |
| Multi-key atomicity across arbitrary keys | Neither — Cluster restricts `MULTI`/`EVAL` to keys in the same slot; Sentinel's single primary just works |
| Managed service (ElastiCache, Redis Cloud, Memorystore) | Probably Cluster under the hood, abstracted away |

### Cluster gotchas

- **Cross-slot operations are forbidden by default.** `MGET key1 key2` works only if both keys hash to the same slot. Use **hash tags** to force them there: `MGET user:{42}:name user:{42}:email` (the braces make Redis hash only `42`).
- **Lua scripts / `MULTI`/`EXEC`** must operate on keys in one slot.
- **`KEYS` / `SCAN`** iterate one node at a time; use `SCAN` per-node if you genuinely need to walk the whole cluster.
- **`FLUSHDB`** on one master does not flush others.
- Client must be cluster-aware (`redis-py` `RedisCluster` class, not `Redis`).

### Python cluster client

```python
from redis.cluster import RedisCluster

r = RedisCluster(host="cluster-endpoint.example.com", port=6379)
r.set("user:42:name", "alice")
r.set("user:{42}:name", "alice")   # hash-tagged; guaranteed slot 42
```

---

## 5. Failure Modes You Need to Plan For

| Failure | Sentinel behavior | Cluster behavior | Mitigation |
|---------|-------------------|------------------|------------|
| Primary crash, replica up to date | Promote replica, ~5–30s client-visible outage | Same, per-shard | Accept; retry writes |
| Primary crash, replica behind by 200ms | Promote replica, lose those 200ms of writes | Same | Durability-critical data → Postgres, not Redis |
| Network partition — primary isolated | Sentinels on majority side promote replica; old primary keeps accepting writes until partition heals, then becomes replica and loses its writes | Same | `min-replicas-to-write` reduces window; accept that Redis is AP, not CP |
| Sentinel quorum lost | No failover — primary stays primary even if it dies | Cluster has its own gossip-based quorum | Deploy sentinels across ≥3 failure domains |
| Out of memory | `maxmemory-policy` kicks in — evicts or rejects writes | Same per-shard | Set `maxmemory-policy` explicitly (`allkeys-lru` for cache, `noeviction` for source-of-truth) |
| Slow `SAVE` / `BGSAVE` fork | Latency spike during snapshot | Same | Disable RDB in cache-only mode, or use `save ""` and rely on replication |
| AOF corruption | Refuses to start; use `redis-check-aof --fix` | Same | Keep off-node RDB backups |
| Slow `BGREWRITEAOF` | Temporary double memory usage | Same | Size instance with ≥2× working set headroom |

---

## 6. Distributed Locks — and Why Failover Makes Them Hard

A common reason teams reach for Redis is a **distributed lock**: "only one worker may run this job at a time." This sits squarely in HA territory because the failure modes above (async replication, failover) are exactly what break naive locks.

### The single-instance lock

For mutual exclusion that is an *optimization* (avoid duplicate work, not a correctness invariant), one command on one Redis instance is enough:

```python
import uuid
from typing import Optional

import redis.asyncio as aioredis

async def acquire(r: aioredis.Redis, lock_key: str, ttl_ms: int = 30000) -> Optional[str]:
    token = str(uuid.uuid4())
    # SET key token NX PX ttl: set only if absent, with an expiry.
    ok = await r.set(lock_key, token, nx=True, px=ttl_ms)
    return token if ok else None
```

Release must be **conditional on owning the lock** — never a bare `DEL`, or you might delete a lock a *different* worker acquired after yours expired. Do it atomically with Lua so the check-and-delete can't race:

```python
RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""

async def release(r: aioredis.Redis, lock_key: str, token: str) -> bool:
    result = await r.eval(RELEASE_LUA, 1, lock_key, token)
    return result == 1
```

The `NX` (set-if-absent), `PX` (expiry so a crashed holder doesn't deadlock the lock forever), and token-checked release are the three things every correct single-instance lock needs.

### Where this breaks: failover

The `SET NX PX` lock lives on **one** primary. Because replication is asynchronous (section 2), this sequence is possible:

1. Worker A acquires the lock on the primary.
2. The primary crashes **before** the lock replicates.
3. Sentinel/Cluster promotes a replica that never saw the lock.
4. Worker B acquires the "same" lock on the new primary.
5. **Two workers now hold the lock simultaneously.**

No amount of client-side cleverness fixes this — it's inherent to Redis being an AP (available, eventually-consistent) store, not a CP consensus system.

### RedLock — and the contested debate

`RedLock` is antirez's algorithm to address this: acquire the lock on a **majority of N independent primaries** (e.g. 3 of 5), so a single failover can't hand out a duplicate. `redis-py` ships it as `redis.lock.Lock` for the single-instance case; multi-instance RedLock needs a library like `redlock-py` / `pottery`.

This is one of the more famous debates in distributed systems, and you should know both sides:

- **Martin Kleppmann's critique** argues RedLock is unsafe for correctness-critical locking: it relies on bounded clocks and timing assumptions, and a GC pause or clock jump on the holder can let two processes believe they hold the lock. His conclusion: if correctness depends on the lock, use a system with a **fencing token** (a monotonically increasing number the protected resource checks), backed by a real consensus store (ZooKeeper, etcd, Consul).
- **antirez's rebuttal** defends RedLock's assumptions as reasonable for many real deployments and disputes the failure model.

**The practical takeaway:**

| Lock is for… | Use |
|--------------|-----|
| Avoiding duplicate work / "best effort" mutual exclusion (the common case) | Single-instance `SET NX PX` + token release. Simple and good enough. |
| Correctness — two holders would corrupt data or double-charge | Don't rely on a Redis lock alone. Use a consensus store (etcd/ZooKeeper) **or** a fencing token the resource enforces. |

If you only remember one rule: **a Redis lock is a performance optimization, not a safety guarantee.** Design so that two simultaneous holders are merely wasteful, not catastrophic.

---

## 7. Managed Redis — What the Provider Handles

For ElastiCache (AWS), Memorystore (GCP), Azure Cache for Redis, Redis Cloud:

- Persistence, replication, Sentinel/Cluster, failover, backup — all automated.
- You still need to decide: **cluster mode on or off**, **number of replicas**, **backup retention**, **`maxmemory-policy`**.
- Failover events are still observable to clients as brief (seconds-to-tens-of-seconds) connection errors. Clients must retry with backoff.
- **TLS by default** for in-transit traffic is now standard on managed services — confirm and enable it.

Running Redis yourself is fine for a single-region, single-tenant setup where you own the operations. For most production teams, managed is the right call — the operational win outweighs the minor config loss.

---

## 8. `maxmemory` and Eviction

This is a setting every production Redis deployment must set explicitly.

```
maxmemory 6gb
maxmemory-policy allkeys-lru
```

| Policy | Behavior | Use when |
|--------|----------|----------|
| `noeviction` | Write returns error when full | Redis is source of truth (queues, rate limiters) — losing data is worse than write rejections |
| `allkeys-lru` | Evict least-recently-used across all keys | Pure cache |
| `allkeys-lfu` | Evict least-frequently-used across all keys | Cache with long-tail access patterns |
| `volatile-lru` | Evict LRU among keys with a TTL only | Mixed-role Redis (rare; prefer splitting) |
| `allkeys-random` | Random eviction | Rare; don't use |

**Default is `noeviction`.** A Redis instance with no `maxmemory` set will happily consume all host RAM and get OOM-killed — which is not a graceful failure. Always set it.

---

## 9. Checklist for Production

- [ ] `maxmemory` and `maxmemory-policy` set explicitly.
- [ ] Persistence decided (both AOF + RDB for source-of-truth; neither for pure cache).
- [ ] TLS in transit; AUTH or ACLs enabled.
- [ ] Sentinel or Cluster (or managed equivalent) — not a single node.
- [ ] ≥3 sentinels (if using Sentinel), across failure domains.
- [ ] `min-replicas-to-write 1` on the primary (if the data matters).
- [ ] Monitoring: `used_memory`, `connected_clients`, `keyspace_hits` / `keyspace_misses`, `master_link_status`, replication lag, eviction count, slowlog length.
- [ ] Clients retry on `LOADING`, `BUSY`, connection errors with backoff.
- [ ] Off-node backup (RDB copied to object storage on a schedule).
- [ ] Chaos test: kill the primary in staging and measure how long writes are unavailable.
- [ ] If you use a Redis lock, confirm two simultaneous holders are merely wasteful, not catastrophic (section 6). Correctness-critical mutual exclusion belongs in a consensus store or behind a fencing token.

---

## See also

- [04_python_clients.md](04_python_clients.md) — `redis-py` client, Sentinel and Cluster wrappers.
- [03_caching_patterns.md](03_caching_patterns.md) — when losing Redis is cheap vs expensive.
- [05_rate_limiting.md](05_rate_limiting.md) — rate-limiter data is source-of-truth-ish; persistence matters.
