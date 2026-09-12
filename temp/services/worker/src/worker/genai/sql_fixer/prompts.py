"""Prompt assembly for bounded SQL repair."""

from html import escape

SYSTEM_PROMPT = """\
Repair a failing PostgreSQL query and execute the corrected query with `execute_sql`.
Use the supplied schema as source data. Produce only read-only SELECT or WITH statements,
always use schema-qualified table names, and change the query after each error. Stop after the
first successful result and return it through the structured output schema.
"""


def build_system_prompt(schema_context: str) -> str:
    return f"""\
{SYSTEM_PROMPT}

<database_schema>
{escape(schema_context.strip())}
</database_schema>
"""


def build_user_prompt(*, intent: str, failing_sql: str, error_message: str) -> str:
    return f"""\
Original intent: {escape(intent)}

<failing_sql>
{escape(failing_sql)}
</failing_sql>

<database_error>
{escape(error_message)}
</database_error>

Repair and execute the query.
"""
