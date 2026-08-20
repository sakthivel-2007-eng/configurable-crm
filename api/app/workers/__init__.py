"""Background workers.

Work that must not happen inside a request handler. M2 ships one: the
indexed-field builder, which issues `CREATE INDEX CONCURRENTLY` and therefore
cannot run inside a transaction block.

Enqueued through `arq` in deployment; invoked directly by the settings service
when no queue is configured, so local development and tests exercise the same
code path.
"""
