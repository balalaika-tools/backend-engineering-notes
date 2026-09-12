# Share Libraries Without Duplicating Service Layers

> **Who this is for**: Engineers deciding what belongs in shared Python packages and how those packages connect to service-owned ports.

## 1. Sharing a database reader does not move the application's contract

Two services need data from the same external database. Copying the query implementation means
fixing it twice. Importing one service's private repository from the other couples their package
structures. A focused shared reader solves that problem while each service keeps its own contract.

The supplied worker uses this arrangement:

```text
InvestigateException needs ExceptionData or a stable source failure
  → worker.ports.investigation.exception_source defines that conversation
  → worker.db.ctc.exception_repository translates it
  → ctc_database fetches data using SQLAlchemy
  → worker adapter converts the library result into worker-owned ExceptionData
```

The adapter remains useful after extraction because it translates both types and failures.
If the library changes its result representation, the service can absorb that change here without
rewriting investigation policy. No second copy of the service port is required inside the library.

**Default:** keep each library organized around the capability it supplies. Add internal ports
when the library itself owns policy that needs replaceable external collaborators. `libs/` states
how code is packaged and reused; it does not declare that code to be domain logic.

This decision guide uses the supplied `temp/libs` and `temp/services` samples. Paths identify
those examples, but their temporary presence is not required to understand the excerpts below.

---

## 2. These three libraries have different architectural responsibilities

After following the reader call above, inspect the smaller package trees:

```text
libs/
├── ctc_database/
│   ├── pyproject.toml
│   ├── src/ctc_database/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── candidates.py
│   │   ├── exceptions.py
│   │   ├── models.py
│   │   └── acceptance.py
│   └── tests/
├── platform_db/
│   ├── pyproject.toml
│   ├── src/platform_db/
│   │   ├── base.py
│   │   └── models/
│   └── tests/contract/
└── platform_observability/
    ├── pyproject.toml
    ├── src/platform_observability/
    │   ├── logging.py
    │   └── providers.py
    └── tests/
```

This is an abbreviated navigation tree. Each `pyproject.toml` declares the installable package's
dependencies and build configuration; `src/` contains its importable Python package. The libraries
have their own test responsibilities without acquiring process entry points or supervisors.

| Library | Shared responsibility | Service responsibility that stays local |
|---|---|---|
| `ctc_database` | Read-only query mechanics, connection construction, data and error contracts | Which data an action needs, translation to its port, configuration and engine disposal |
| `platform_db` | SQLModel table mappings and database schema metadata | Use cases, transaction scope, repository queries, domain views |
| `platform_observability` | Logging and OpenTelemetry provider setup and shutdown mechanics | Service identity, resolved configuration, when to initialize and close providers |

SQLModel classes in `platform_db` are object-relational mappings, or **ORM models**: Python
representations of database tables. Sharing them does not make them pure domain objects.
OpenTelemetry providers configure tracing, metrics, and log export; their shared implementation
still belongs at the infrastructure edge of each process.

`ctc_database/exceptions.py` queries business exception records. The name is understandable in
that domain but can be mistaken for Python error definitions. `exception_reader.py` would improve
navigation if the project chooses to rename it; this is a naming preference, not an architectural
reason to add layers.

> **Core:** classify a shared package by what it knows and does. An internal database library is
> just as concrete a database dependency as an external one.

---

## 3. A local adapter earns its place through translation

The worker's CTC adapter imports both `ctc_database` and worker-owned port types. The shared reader
does not need to know the worker exists. An explanatory excerpt from the adapter's failure
translation is:

```python
try:
    result = await self._reader.fetch(exception_id, rec_schema=rec_schema)
except ctc_database.ExceptionNotFoundError as exc:
    raise ExceptionNotFoundError(exception_id) from exc
except ctc_database.NoLinkedRecordsError as exc:
    raise NoLinkedRecordsError(exception_id) from exc
except ctc_database.ExceptionSourceUnavailableError as exc:
    raise ExceptionSourceUnavailableError(
        f"Failed to fetch CTC exception {exception_id!r}"
    ) from exc
```

The unqualified errors come from `worker.ports.investigation.exception_source`, not from the
library. Successful results are also mapped: the adapter copies the exception identity, version,
view, and linked records into the worker's `ExceptionData` and `LinkedRecord` types.

Both error classes may currently have the same name and message. Their separate ownership still
has value: one describes the library API, while the other describes outcomes the worker promises
to understand. The adapter is the place where that compatibility is maintained.

```text
library raises source unavailable
  → adapter raises worker-owned source unavailable
  → InvestigateException classifies a transient failure
  → failure recorder and delivery adapter arrange the next attempt
```

Do not wrap every shared function automatically. A pure calculation with a stable, suitable
signature can be called directly. If a concrete library already satisfies an application-owned
protocol, bootstrap may inject it directly when the type and error semantics also match.
A forwarding wrapper with no translation, isolation, or behavior adds navigation without value.

**Success signal:** replacing the shared reader with a deterministic fake tests investigation
policy without SQLAlchemy objects; adapter tests separately prove result and failure conversion.
If the action must catch `ctc_database` errors, the service has accepted that library's contract
directly and no longer has the isolation described above.

---

## 4. Shared table models create a compatibility obligation

The orchestrator writes investigations that the worker later updates. Both use `platform_db`,
and the migration process imports its metadata. That is a shared schema contract across
deployables, even though each service's application remains behind ports.

In the worker repository, the distinction is visible in these imports:

```python
from platform_db import Investigation
from platform_db import InvestigationStatus as DatabaseStatus
from worker.domain.investigation import InvestigationState, InvestigationStatus
```

`Investigation` carries persistence mapping details. `InvestigationState` carries the state the
worker needs to reason about execution. The repository converts between them so application code
does not depend on an ORM session or a table model's loading behavior.

Now consider a proposed schema change, not an existing sample migration:

```text
old worker reads statuses: queued, processing, completed, failed
new writer starts persisting status: paused
old worker receives paused → its status conversion may fail
```

Adding a value to a shared model is not enough to make independently running old code understand
it. Rollout compatibility must account for the database schema, library versions, and readers and
writers already deployed. A coordinated change might deploy compatible readers before enabling
new writes; a column change might first add an optional field, then populate it, and only later
enforce stricter constraints after old consumers are gone.

The migration process owns schema changes; service startup should not silently mutate tables
because a library was imported. Keep schema compatibility checks and integration tests close to
that contract. In the sample, `platform_db/tests/contract/test_model_ddl.py` inspects generated
table definitions, while `platform_migrations/tests/integration/test_schema_parity.py` checks the
migrated schema against model metadata. Neither alone proves every mixed-version rollout safe.

⚠️ Sharing models prevents duplicated definitions but also couples releases through their data.
The first symptom may be an old worker rejecting a new enum value or requiring a column that has
not been migrated. Clean Python imports cannot prevent that failure.

Do not treat shared tables as a default for independently owned services that require separate
data evolution. A service API or versioned event contract can provide a narrower integration
boundary when that independence is the actual requirement.

---

## 5. The caller controls resource lifetime even when construction is shared

A library can know how to create a database engine without knowing how long a worker should live.
`ctc_database` explicitly leaves configuration and engine disposal to its consumers. Bootstrap
supplies settings, calls its factory, and registers cleanup.

For telemetry, the sample's ownership chain is:

```text
worker.config.Settings
  → worker.bootstrap.observability maps selected settings into TelemetryConfig
  → platform_observability configures providers and logging
  → worker bootstrap registers shutdown for the process lifecycle
```

The library accepts its own explicit configuration type instead of importing the worker's settings.
This lets the orchestrator use the same mechanics with a different service identity. Although the
library tracks process-wide provider state internally, the consumer chooses when initialization
and shutdown happen. Reusable code does not have to be stateless; its state needs a clear owner.

If provider setup succeeds but logging setup fails, the service's configuration adapter closes
the acquired providers before propagating the failure. That prevents partial initialization from
leaking resources. [Runtime composition](06_compose_the_runtime_at_the_edge.md#6-partial-startup-needs-cleanup-before-a-runtime-exists)
demonstrates the same cleanup principle with a runnable standard-library example.

**Success signal:** two services can supply different configuration values through the same
public library API, and setup-failure tests observe cleanup. If importing the library starts a
consumer loop or reads one deployable's settings, process ownership has leaked into reuse.

---

## 6. Add library ports when the library has its own policy to protect

A database integration is intentionally coupled to database technology. Recreating a full hexagon
inside it would often give every query another interface without isolating a new decision.
But a reusable document-processing library might own meaningful processing policy while requiring
caller-supplied storage and extraction capabilities.

For that different library, an initial structure could be:

```text
document_processing/
├── process.py       # Coordinates reusable processing policy
├── contracts.py     # Storage/extraction protocols and stable results
└── values.py        # Document identity and validation rules
```

A caller injects an object store and extractor; library tests inject local fakes. If those
responsibilities grow, `contracts.py` may become `ports/`, and bundled implementations may earn
`adapters/`. The need comes from policy and dependency boundaries, not from being under `libs/`.

The same distinction explains why a focused `genai/shared/` package can hold genuinely shared AI
invocation mechanics, while a root `common/` bucket usually makes ownership harder to find.
Reuse with a named responsibility is different from grouping everything used twice.

> **Key insight**: extract the stable capability, keep each caller's decisions local, and add
> boundaries inside a library only when the library has decisions of its own to preserve.

---

**Next**: return to the [service-code reading path](README.md#read-the-service-and-library-samples),
or use [the migration review](11_migrate_and_review_an_existing_service.md) to assess one proposed extraction.
