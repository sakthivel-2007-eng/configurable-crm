"""CLI for the first account.

    uv run python -m app.bootstrap --email you@example.com \\
        --name "Your Name" --workspace "Your Company"

Run once per deployment, by whoever installs it. Prints a single-use link for
the owner to set their password; everyone else arrives by invitation from
inside the app.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.bootstrap.first_account import create_first_account
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.bootstrap",
        description="Create the first workspace and its owner.",
    )
    parser.add_argument("--email", required=True, help="The owner's email address")
    parser.add_argument("--name", required=True, help="The owner's full name")
    parser.add_argument("--workspace", required=True, help="The workspace name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even though this deployment already has users.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            result = await create_first_account(
                session,
                settings,
                email=args.email,
                full_name=args.name,
                workspace_name=args.workspace,
                force=args.force,
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(f"created workspace {result.workspace_name!r} owned by {result.email}")
    print()
    print("  Send them this link — it works once and expires in 7 days:")
    print(f"  {result.set_password_url}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args, get_settings()))


if __name__ == "__main__":
    raise SystemExit(main())
