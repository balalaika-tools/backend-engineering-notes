# Kafka Security Must Constrain Both Connection and Action

> **Who this is for**: engineers moving beyond a plaintext local broker.

## An authenticated producer can still be over-privileged

**Transport Layer Security (TLS)** encrypts traffic; the **Simple Authentication and Security
Layer (SASL)** or mutual TLS authenticates a service identity; **access-control lists (ACLs)**
authorize that identity to named cluster resources. Missing any layer leaves a different gap.

---

## 1. Start from one service identity

Give `orders-api` write access to `orders.events.v1`, and `billing-worker` read access plus its own
consumer group. Do not share credentials across applications: attribution and revocation disappear.
Keep secrets outside source, images, logs, and event payloads; rotate them with overlapping validity.

This `confluent-kafka` producer authenticates `orders-api` with SASL/SCRAM over verified TLS.
**SCRAM** is SASL's challenge-response password mechanism. The certificate-authority file makes
TLS reject an untrusted broker certificate.

```python
import os

from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "broker-1.example.com:9093,broker-2.example.com:9093",
    "security.protocol": "SASL_SSL",
    "sasl.mechanism": "SCRAM-SHA-512",
    "sasl.username": "orders-api",
    "sasl.password": os.environ["KAFKA_ORDERS_API_PASSWORD"],
    "ssl.ca.location": "/etc/kafka/ca.pem",
    "client.id": "orders-api",
})
```

Authentication is not authorization. Apply these access-control lists with an authenticated admin
configuration in `admin.properties`:

```bash
kafka-acls.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --add \
  --allow-principal User:orders-api --operation Write \
  --topic orders.events.v1

kafka-acls.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --add \
  --allow-principal User:billing-worker --operation Read \
  --topic orders.events.v1

kafka-acls.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --add \
  --allow-principal User:billing-worker --operation Read \
  --group billing-v1
```

The principal/action/resource mapping is deliberate: `orders-api` may write only the event topic;
`billing-worker` may read that topic only while joining `billing-v1`. A write to `payments.events.v1`
must return `TOPIC_AUTHORIZATION_FAILED`, and using group `fraud-v1` must return
`GROUP_AUTHORIZATION_FAILED`. If both negative tests succeed, a wildcard or prefixed ACL is broader
than this policy and should be inspected with `kafka-acls.sh --list`.

An attacker who can create topics or alter configs can redirect or disrupt data even without broker
shell access. Admin APIs and Kafka Connect's plugin/REST surfaces require tighter trust boundaries.

**Success signal:** the intended write/read succeeds while a test write to an unrelated topic and
read using another group's ID are denied. A successful TLS handshake alone silently proves no
authorization.

> **Key insight**: Kafka security is the intersection of authenticated identity, resource action,
> network path, and data classification—not a single “secure protocol” setting.

---

## 2. What breaks, and when not to share a cluster

⚠️ Wildcard ACLs turn one compromised client into a cluster-wide producer or consumer.

Do not rely on ACLs alone for tenants requiring hard resource, encryption-key, or failure-domain
isolation. Use separate clusters or a managed-service isolation boundary.

---

**Next**: [Capacity Planning and Performance](02_capacity_planning_and_performance.md)
