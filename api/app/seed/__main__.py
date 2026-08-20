"""CLI for the demo seed.

    uv run python -m app.seed --workspace demo --leads 50000 --seed 42

`--workspace` names the run, not a real customer: this command exists to build
a fixture and refuses to do anything else. There is deliberately no flag that
points it at an existing workspace, because "seed my production tenant with
example data" is not an operation this product should be able to perform.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import Settings
from app.db import create_engine, create_session_factory
from app.seed.demo import DemoSeeder


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed",
        description="Build the fictional Northwind Tutors demo workspace.",
    )
    parser.add_argument(
        "--workspace",
        default="demo",
        help="A label for this run. Never an existing workspace — a new one is always created.",
    )
    parser.add_argument("--leads", type=int, default=50_000, help="How many leads to generate")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed. Fixed by default so two runs produce identical data.",
    )
    return parser.parse_args(argv)


async def _run(leads: int, seed: int) -> int:
    settings = Settings()
    if not settings.is_local:
        # The one guard that matters. A demo taxonomy in a customer's database
        # is the failure CLAUDE.md spends a section warning about, and it is
        # not recoverable by deleting rows — people will have built on it.
        print(
            f"refusing to seed: ENVIRONMENT is {settings.environment!r}, not 'local'.\n"
            "The demo workspace is a test fixture and must never reach a real deployment.",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(settings)
    factory = create_session_factory(engine)

    async with factory() as session:
        result = await DemoSeeder(session, engine, lead_count=leads, seed=seed).run()

    await engine.dispose()

    print(f"seeded {result.workspace_slug} ({result.workspace_id})")
    print(f"  {result.leads:,} leads, {result.actions:,} actions in {result.seconds}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args.leads, args.seed))


if __name__ == "__main__":
    sys.exit(main())
