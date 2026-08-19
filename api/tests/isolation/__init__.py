"""Cross-workspace isolation suite.

This package grows every milestone. When a milestone adds an endpoint that
accepts a resource id, add it to `M1_RESOURCE_ROUTES` in
`test_cross_workspace.py` (or the equivalent list for that milestone) — the
parametrised tests then cover it automatically.

The invariant under test, stated once:

    A member of workspace A receives 404 for workspace B's data — by direct id,
    by list, and by filter — and never 403.
"""
