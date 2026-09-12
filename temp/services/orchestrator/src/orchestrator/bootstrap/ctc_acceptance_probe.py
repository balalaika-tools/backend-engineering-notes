"""Run read-only CTC acceptance inside the orchestrator deployment environment."""

import asyncio
import json
from dataclasses import asdict

import ctc_database
from orchestrator.config.secrets import get_secrets
from orchestrator.config.settings import get_settings


async def run() -> int:
    try:
        settings, secrets = get_settings(), get_secrets()
    except (ValueError, OSError):
        print(json.dumps({"accepted": False, "check": "configuration"}))
        return 1
    engine = ctc_database.build_engine(
        database_url=ctc_database.authenticated_database_url(
            str(settings.ctc_database_url), secrets.ctc_db_password
        ),
        pool_size=1,
        acquisition_timeout_seconds=settings.ctc_acquisition_timeout_seconds,
    )
    try:
        report = await ctc_database.verify_candidate_contract(
            engine,
            schema=settings.ctc_reconciliation_schema,
            exception_name=settings.exception_name,
            statement_timeout_seconds=settings.ctc_statement_timeout_seconds,
        )
    except (ctc_database.CtcAcceptanceError, ctc_database.ExceptionSourceUnavailableError) as exc:
        print(
            json.dumps(
                {
                    "accepted": False,
                    "schema": settings.ctc_reconciliation_schema,
                    "exception_name": settings.exception_name,
                    "check": str(exc)
                    if isinstance(exc, ctc_database.CtcAcceptanceError)
                    else "ctc_database_unavailable",
                }
            )
        )
        return 1
    finally:
        await engine.dispose()
    print(json.dumps({"accepted": True, **asdict(report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
