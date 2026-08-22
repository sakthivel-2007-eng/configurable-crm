"""The `arq` worker's entry point (M8/M10).

These assert the *shape* `arq` requires, not behaviour — `tick` and `dispatch`
have their own tests, and calling them directly is exactly why this file has to
exist.

`WorkerSettings.redis_settings` shipped as a `@staticmethod`. It type-checked,
imported cleanly, and passed every test in the suite; the container then died on
the first line of the real worker with `'staticmethod' object has no attribute
'host'`, because `arq` reads it as an attribute and immediately asks it for
`.host`. The only thing that caught it was running `docker compose up`.

So: cheap assertions about the contract with the framework, which is the part
unit tests of our own functions can never reach.
"""

from __future__ import annotations

import pytest
from arq.connections import RedisSettings

from app.workers.scheduler import WorkerSettings, dispatch, tick


def test_the_arq_entry_point_matches_what_arq_expects() -> None:
    """`redis_settings` is an attribute holding a `RedisSettings`.

    A method here passes mypy and every unit test, and fails 100% of the time
    in production.
    """
    settings = WorkerSettings.redis_settings
    assert isinstance(settings, RedisSettings), (
        f"arq reads this as an attribute and asks it for .host; got {type(settings).__name__}"
    )
    assert settings.host
    assert settings.port


def test_both_cron_jobs_are_registered() -> None:
    """The scheduler tick and the outbox dispatcher.

    Losing one is silent: schedules simply stop arriving, or webhooks stop
    being delivered, with nothing in any log to say a job was never registered.
    """
    registered = {job.coroutine for job in WorkerSettings.cron_jobs}
    assert registered == {tick, dispatch}, {job.name for job in WorkerSettings.cron_jobs}


@pytest.mark.parametrize("hook", ["on_startup", "on_shutdown"])
def test_the_lifecycle_hooks_are_callable(hook: str) -> None:
    """`on_startup` builds the engine, session factory and transports.

    Without it every job raises `KeyError` on the first `ctx[...]` lookup.
    """
    assert callable(getattr(WorkerSettings, hook))
