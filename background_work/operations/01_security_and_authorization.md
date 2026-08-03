# Background Work Is a Privileged Command Path

> **Who this is for**: Backend engineers securing APIs, schedulers, brokers, workers, and operator tools that can create or change durable work.

Before reading this, understand named transitions and durable intent in **[A Database-Backed State Machine](../state_machines/02_database_backed_state_machine.md)**.

---

## 1. An asynchronous boundary does not preserve authorization

A user is allowed to generate reports for tenant A. The API accepts `tenant_id=B`, writes a job, and the worker later runs with a service credential that can read every tenant. Authentication succeeded, queue delivery succeeded, and the wrong customer's data was exported.

Moving work to a queue changes when and where code runs; it does not carry the caller's authority across that boundary. The command service must authorize the requested business action before durable intent exists, bind the trusted tenant and actor to that intent, and give the worker only the capability needed to execute it.

```text
untrusted request or signal
          │
          ▼
authenticate identity
          │
          ▼
authorize named command against tenant + resource + current state
          │
          ▼
commit workflow/job/outbox + authorization evidence
          │
          ▼
publish ID-only hint ──► worker reloads authoritative rows
                              │
                              └── executes with a narrow service identity
```

> **The near-miss**: a signed or authenticated queue message proves who sent bytes to the broker. It does not prove that the original user could approve this workflow, access this tenant, or redrive this effect. Transport identity and business authorization are different decisions.

---

## 2. Authorize commands, not arbitrary target states

The authorization decision needs the same inputs as the workflow decision:

```text
principal       tenant          resource
requested event current state   expected version
evidence        policy version  request/command ID
```

The default controlled commands are **create**, **approve**, **cancel**, and **redrive**. Administrative overrides should remain exceptional rather than becoming a generic `set_state` endpoint.

| Command | Required authorization question | Durable evidence |
|---|---|---|
| **Create** | May this principal start this workflow type for this tenant and input? | Principal, tenant, policy/version, normalized input hash |
| **Approve/reject** | May this principal make this decision for the current resource and state? | Reviewer, reason, expected workflow version |
| **Cancel** | May this principal stop this run, including any compensation consequence? | Actor, authorization basis, reason, expected version |
| **Redrive** | May this operator repeat or recover this exact business effect? | Operator, incident/change reference, original operation key |
| Change priority | May this operator move this tenant ahead of others? | Actor, old/new class, expiry, reason |
| Override quota | May this operator increase cost or capacity exposure? | Actor, limit delta, expiry, approval reference |

The application accepts a named event such as `approve`; it derives `GENERATION_QUEUED` from the versioned graph. Authorization happens against the named event and current resource. The database write then compares the expected state and version so a permission checked against version 7 cannot silently modify version 8.

> **Key insight**: authorization is part of the durable command, not middleware metadata. If recovery cannot explain who was allowed to create the owed work and under which policy, the system has preserved execution without preserving authority.

---

## 3. Tenant identity comes from trusted state, not the message

An API derives `tenant_id` from the authenticated principal's membership and the resource it loaded. A scheduler derives it from the schedule row. A webhook maps a verified external account to an internal tenant. None accepts an unverified tenant identifier as authority.

```text
API token ───────────────► membership/resource lookup ──► trusted tenant_id
schedule_id ─────────────► schedule owner row ──────────► trusted tenant_id
verified webhook account ► integration mapping ────────► trusted tenant_id
queue payload tenant_id ────────────────────────────────► correlation only
```

Persist `tenant_id` on workflows, jobs, operation records, and audit history. Enforce relational consistency so a job cannot point at a workflow from another tenant. Include tenant scope in idempotency and uniqueness contracts—for example, a key may be globally derived as `tenant:{tenant_id}:report:{date}`, or enforced with `UNIQUE (tenant_id, idempotency_key)`.

Workers treat a message as an ID-only wake-up hint. They load the job, workflow, tenant, current state, and policy-relevant fields from the authoritative store. A payload that says `tenant_id=B` while the job belongs to A is rejected and quarantined; it never changes the query scope.

Database row-level security can provide defense in depth when connection identity preserves tenant context. It is not a substitute for application authorization when every worker uses one service account with unrestricted bypass privileges.

---

## 4. Broker access is permission to request execution

Anyone who can publish a valid task name and arguments may be able to invoke a registered worker action. Treat the broker as a privileged execution plane, even when every application endpoint is authenticated.

```text
API/outbox publisher identity
  └── publish only to owned exchange/topic/queue

worker identity
  ├── consume only its workload queues
  ├── read/claim only compatible job rows
  └── call only the downstream services needed by that workload

operator identity
  └── inspect and submit named control commands; no arbitrary row edits
```

Use separate credentials for publishers, consumers, and administrators; private network paths; encrypted transport; and broker namespace permissions. Do not give the web process permission to consume or delete messages merely because it publishes them. Do not give a notification worker the payment worker's database role or provider credential.

Message signing can detect unauthorized modification or publication when the runtime supports it. Signing does not encrypt payloads, revoke an already published command, enforce tenant authorization, or make a replay safe. Stable command and operation keys still reject duplicates.

---

## 5. Workers execute capabilities without impersonating users

The worker usually runs as a service principal, not as the original user. Store the initiating actor for audit, but do not copy a long-lived user bearer token into the job payload. Tokens expire, leak through broker tooling and DLQs, and often carry more authority than the worker needs.

Prefer this boundary:

```text
job payload: job_id + expected workflow version + stable operation key
worker identity: narrow machine credential
authoritative row: tenant + approved command + sanitized input/reference
downstream call: tenant-scoped resource ID + stable idempotency key
```

If permission revocation must stop work that was previously admitted, make that a documented execution-time policy: persist `authorization_epoch` or cancellation state, reload it before the irreversible effect, and fail terminally when it no longer matches. Do not accidentally re-evaluate every retry against today's policy unless the product has chosen that semantic; a harmless policy edit could otherwise strand old work forever.

Keep secrets and personal data out of queue messages, transition metadata, logs, and DLQ copies. Pass immutable object references and hashes, then authorize the worker's read at the storage boundary. Encrypting the broker does not remove data already copied into dashboards, traces, or dead-letter exports.

---

## 6. Operator recovery is a high-privilege workflow

Cancellation, redrive, quota override, and forced compensation can repeat external effects or move work ahead of other tenants. Expose them as named commands with the same controls as customer commands:

1. Authenticate the operator with a distinct role.
2. Authorize the exact action and tenant/resource scope.
3. Require the current workflow version and a reason.
4. Preserve the original operation key when recovering the same effect.
5. Record the old and new job/run identifiers plus the resulting version.
6. Require a second approver for actions whose business risk warrants it.

The default path is the first five steps; dual approval is for high-impact domains, not every retry. Operator tooling should show the current effect evidence before enabling redrive. A console button that copies a DLQ payload to the live queue bypasses the command boundary and should not exist.

---

## 7. Security tests prove that denial creates no durable work

The core security suite covers **cross-tenant create**, **stale approval**, **unauthorized redrive**, and **direct broker publication**.

| Test | Injection | Required evidence |
|---|---|---|
| **Cross-tenant create** | Principal for A supplies B's resource/job identifiers | Generic denial; zero workflow, job, outbox, and provider rows for B |
| **Stale approval** | Authorized reviewer submits version 7 after cancellation created version 8 | Explicit conflict; no generation job or approval history |
| **Unauthorized redrive** | Read-only operator submits a failed job | Denial plus security audit; original job remains terminal |
| **Direct broker publication** | Publish a guessed job ID without a matching claimable row | Worker records rejection/quarantine and performs no effect |
| Tampered tenant hint | Message tenant differs from authoritative job tenant | Payload value ignored or message quarantined; no cross-tenant read |
| Revoked execution policy | Authorization epoch changes before an irreversible step | Terminal/cancelled decision according to policy; no provider effect |

**How you know it is working**: every denied command leaves an authentication/authorization decision that can be correlated by request or message ID, while business tables show no new owed work. The common silent failure is a `403` response after a job or outbox row was already committed; assert row absence, not only the HTTP status.

---

## 8. Keep the security boundary proportional to the system

For one trusted internal producer and one worker in the same deployment, separate service accounts, ID-only jobs, conditional state checks, and an audited operator path may be enough. Do not introduce a policy engine merely to rename a small authorization function.

Use stronger tenant-isolated credentials, queue namespaces, storage policies, and approval controls when workloads cross teams, customers, regions, or regulated data boundaries. Use a dedicated secrets manager and short-lived workload identity when static credentials would be copied across many workers.

⚠️ A worker that queries `WHERE id = :job_id` and only later checks `tenant_id` may already have disclosed result or payload data through logs or errors. Tenant scope belongs in the first authoritative lookup.

⚠️ A permission check performed after job creation can return a clean denial while the worker still executes the committed intent. Authorization must precede or participate in the transaction that creates work.

⚠️ A broker reachable with producer credentials shared by every service turns compromise of the least trusted service into permission to request the most privileged task.

Do not use end-user delegation when the downstream action is properly owned by the service; use a narrow machine identity and durable initiating-actor evidence. Do not use a machine identity to erase business accountability for approvals or operator recovery.

---

**Next**: [Part 2: Multitenancy, Admission, and Fairness](02_multitenancy_admission_and_fairness.md)
