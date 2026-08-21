"""The demo workspace — a fixture, not a default.

`docs/01-data-model.md` §8. This builds a **fictional** workspace so that tests
and performance work have something non-trivial to run against. It is the single
most dangerous module in the repository to misread, so, plainly:

> Nothing in here is a default, a starter template, or a suggestion. A real
> workspace gets what `WorkspaceProvisioner` gives it — four fields, four
> stages, a lost-reason set, seven dispositions, five permission templates —
> and not one row of what follows.

CLAUDE.md calls seeding a taxonomy "the #1 mistake here". The protection is that
every business-shaped name below belongs to *Northwind Tutors*, a company that
does not exist, and reaches the database only through this module, which the
application never imports.

    uv run python -m app.seed --workspace demo --leads 50000 --seed 42
"""

from __future__ import annotations

from app.seed.demo import DemoSeeder, SeedResult

__all__ = ["DemoSeeder", "SeedResult"]
