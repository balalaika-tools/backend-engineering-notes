# Secure JetStream at Connection, Subject, and Account Boundaries

> **Who this is for**: engineers moving from a trusted local server to shared production NATS.

## Walk the unauthorized publish before choosing controls

Suppose a compromised reporting service can connect and publish `orders.eu.refund-approved`.
Because the `ORDERS` stream captures `orders.>`, the forged event is durable and every authorized
consumer may process it. Encryption alone does not prevent this; the attacker is already a client.

The defense needs three distinct decisions:

```text
TLS verifies/encrypts the network peer
credentials authenticate which NATS user connected
subject permissions authorize what that user may publish or subscribe
```

---

## 1. Grant applications the smallest subject surface

A publisher that only creates orders should publish `orders.*.created`, not `orders.>`. A worker
should subscribe only to its delivery subjects and publish only the acknowledgment or application
subjects required by its client protocol. JetStream management uses API subjects under `$JS.API.>`;
do not grant broad management access to ordinary publishers.

This illustrative static configuration shows the shape; use generated credentials rather than
literal passwords in a repository:

```hcl
accounts {
  ORDERS_APP {
    jetstream: enabled
    users: [
      {
        user: order_api
        password: $ORDER_API_PASSWORD
        permissions: {
          publish: ["orders.*.created"]
          subscribe: ["_INBOX.>"]
        }
      }
    ]
  }
}
```

The reply inbox permission is needed because request/reply operations—including publish
acknowledgments—receive server responses on an inbox. Test the actual client calls with the proposed
permissions; a connection succeeding does not prove the JetStream API exchange is authorized.

> **Core:** authenticate every production client and allow only the publish, subscribe, and reply
> subjects required by its concrete interactions.

> **Production:** for larger installations, prefer operator, account, and user JWTs managed with
> `nsc` so trust and limits are signed rather than copied into every server configuration.

---

## 2. Accounts are isolation domains, not naming prefixes

A NATS **account** isolates subject namespaces, connections, subscriptions, and JetStream resources.
Two accounts may each use `orders.created` without seeing each other. Explicit exports and imports
cross that boundary; a subject prefix such as `tenant_a.orders` inside one account does not provide
the same isolation.

Use separate accounts when tenants require independent quotas, administrators, or blast radius.
Use users and subject permissions inside one account when services share one governed event domain.

Account-level JetStream limits bound storage, memory, streams, and consumers so one tenant cannot
consume the whole cluster. Server-wide limits remain necessary because account maxima can sum to
more physical capacity than exists.

> **Key insight**: subjects partition routing inside an account; accounts partition trust and
> resource ownership across clients.

---

## 3. TLS and credential rotation need observable failure modes

Require TLS for client, route, gateway, and leaf-node links that cross an untrusted network. Verify
server names and trust roots in clients; disabling certificate verification turns encryption into
an unauthenticated tunnel.

Rotate user credentials with overlap: issue the new credential, deploy clients, observe old-identity
connections drain, then revoke the old credential. **Success signal:** authentication failures stay
at baseline, all expected services reconnect with the new identity, and the old credential is
rejected in a controlled test.

Never log seed keys, bearer credentials, JWT user credentials, or full connection URLs containing
secrets. Logs should identify the public user/account identity and authorization decision instead.

---

## 4. What breaks, and when not to share an account

⚠️ Granting publish to `>` lets a compromised service forge application events and target NATS
control subjects. The stream accepting a message is not proof that the publisher was legitimate.

⚠️ Exposing port `8222` publicly leaks topology, connection, subscription, and JetStream metadata.
Bind monitoring privately and put authentication in the collection path.

Do not share one account across mutually untrusted tenants or teams with independent compliance
boundaries. Subject conventions are valuable organization, but they are not a security boundary
without enforced permissions.

> **Edge case:** response permissions can grant temporary publish rights to reply subjects for
> request/reply services. Use them instead of a permanent broad inbox grant when the exact service
> interaction warrants the added policy complexity.

The [official security deep dive](https://docs.nats.io/learn/security/) covers centralized and
decentralized authentication, accounts, and TLS.

---

**Next**: [When to Use JetStream](08_when_to_use_jetstream.md)
