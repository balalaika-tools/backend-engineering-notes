# Run a Type Checker as an Engineering Feedback Loop

> **Who this is for**: Python developers who can read annotations but have not
> yet made a static type checker part of local development and continuous
> integration.

## The first useful check takes one file and one command

A type annotation does nothing when nobody checks it. Given an active project
environment with Python 3.11+ and `uv` installed, save this as `contracts.py`:

```python
def order_total(unit_price: float, quantity: int) -> float:
    return unit_price * quantity


total = order_total(12.50, 4)
print(total)
```

Run mypy without adding it permanently to the project yet:

```bash
uvx mypy --strict contracts.py
```

The success signal is:

```text
Success: no issues found in 1 source file
```

> **Core:** keep the first contract tiny: one annotated function, one valid
> call, and one deliberately invalid call prove that the checker is installed,
> the file is in scope, and argument compatibility is enforced.

Now change the call to `order_total(12.50, "4")`. The checker points at the
argument before that value reaches production:

```text
contracts.py:5: error: Argument 2 to "order_total" has incompatible type "str"; expected "int"  [arg-type]
Found 1 error in 1 file (checked 1 source file)
```

If the command reports that it checked no files, or it succeeds after the bad
call is uncommented, verify the path passed to mypy and make sure a broad
`exclude` pattern is not hiding the file.

> **Key insight**: annotations become an engineering control only when the same
> checker, configuration, and scope run locally and in continuous integration.

---

## 1. The checker follows contracts without running the program

The bad call above is syntactically valid Python. It fails later only if the
executed path multiplies a float by a string. A **static type checker** reads the
program and compares values against declared contracts without executing its
business logic.

That distinction explains both its value and its limit:

```text
source code + annotations + imported type information
                         │
                         ▼
                    type checker
                    │          │
             consistent     contradiction
                    │          │
                 success    file:line diagnostic
```

Mypy cannot prove that an HTTP payload really contains an integer. Runtime
validation must establish that fact at the trust boundary; typing then carries
the validated fact through the rest of the application.

> **The near-miss**: a type checker is not a Python test runner with extra
> assertions. It analyzes possible calls; tests execute selected calls. You need
> both because either one can find failures the other cannot.

---

## 2. Put one owned configuration at the repository root

Command-line flags drift when every developer remembers a different set. Put
the policy in `pyproject.toml` so editor integrations, local commands, and
continuous integration read the same artifact:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_unreachable = true
show_error_codes = true
files = ["app", "tests"]
```

Then the repository command is simply:

```bash
uv run mypy
```

**Success signal:** mypy names the configured source-file count and exits with
status `0`. A nonzero status plus `Cannot find implementation or library stub`
usually means a dependency is absent from this environment or does not publish
type information; it does not mean every error from that package should be
globally ignored.

`strict = true` is a useful default for a new codebase. The exact checks grouped
under strict mode can change between mypy releases, so pin the project's mypy
version and review upgrades. In an existing untyped codebase, start with a
bounded package and expand the `files` list rather than adding a repository-wide
ignore.

> **Production:** make mypy a development dependency so `uv run mypy` uses the
> pinned environment:
>
> ```bash
> uv add --dev mypy
> ```
>
> The success signal is a mypy entry in `pyproject.toml` and a changed `uv.lock`.
> If neither changes, the command ran outside the intended project root. `uvx`
> is ideal for the first experiment, not for a CI policy that must remain
> reproducible.

---

## 3. Make CI run the same command, not a similar one

The continuous-integration (CI) job should install the locked development
environment and run exactly:

```bash
uv run mypy
```

Do not pass a narrower directory in CI than the `files` scope used locally. A
green check that covered only `app/api` while the changed code lives in
`app/workers` is a silent no-op.

Keep the check required on the protected branch. If teams may merge while the
job is optional, annotations quickly become aspirational documentation and
errors accumulate until enabling the checker becomes a migration project.

---

## 4. Use diagnostics to repair the boundary, not silence the symptom

Most useful type-checking work falls into three moves:

| Diagnostic shape | First question | Typical correction |
|---|---|---|
| Wrong argument or return type | Is the contract or the value wrong? | Convert/validate at the boundary, or correct the annotation |
| `Optional`/`None` access | Which branch proves the value exists? | Add a real guard or redesign the return contract |
| Missing attribute after a union | What observation distinguishes the variants? | Narrow with `isinstance`, a discriminant, or `TypeGuard` |

Use `reveal_type(value)` temporarily when the checker's inferred type surprises
you. Remove it after the investigation; mypy deliberately emits a note for each
call.

Avoid reflexive `cast()` and `# type: ignore`. Neither changes the runtime value.
If an ignore is unavoidable at a third-party boundary, target one error code and
explain the external invariant:

```python
result = legacy_client.fetch()  # type: ignore[no-untyped-call]  # upstream has no stubs
```

That comment is technical debt with a visible reason, not proof that `result`
has the type later code assumes.

---

## 5. What breaks first, and when static typing is the wrong tool

⚠️ The first failure is unchecked surface area. Untyped functions and values of
type `Any` allow mistakes to flow through otherwise strict code without a
diagnostic. Track which packages are checked and shrink untyped boundaries.

⚠️ Globally enabling `ignore_missing_imports` can hide a misspelled import as easily
as a genuinely untyped dependency. Prefer a narrow per-module override after
confirming the library lacks usable type information.

Static typing does not replace runtime validation for requests, messages,
configuration, database rows, or third-party responses. It also cannot prove
business properties such as “the user owns this order” or “this retry will not
double-charge.” Use Pydantic or explicit parsing at trust boundaries, tests for
behavior, and domain checks for runtime invariants.

For short exploratory scripts, a strict repository setup may cost more than it
returns. Add it when code becomes shared, long-lived, or expensive to break.

---

## 6. Verify the repository contract from a clean environment

Before treating the checker as enforced, verify all three paths:

1. the valid baseline exits `0`;
2. one deliberate bad call exits nonzero with `[arg-type]`;
3. the CI job runs the same configured scope from a clean checkout.

The second check proves more than a green run: it demonstrates that the file is
actually inside the checker's reach. Remove the deliberate error after the
failure is observed.

Current command and configuration behavior: [mypy getting started](https://mypy.readthedocs.io/en/stable/getting_started.html),
[mypy configuration](https://mypy.readthedocs.io/en/stable/config_file.html), and
[mypy command line](https://mypy.readthedocs.io/en/stable/command_line.html), checked 2026-09-10.

---

**Next**: [Python Typing — contracts used across backend code](typing.md)
