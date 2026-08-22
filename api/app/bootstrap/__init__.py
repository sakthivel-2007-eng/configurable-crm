"""Creating the first account in a deployment (M11).

The workspace model is invite-only: there is no registration endpoint, and an
account exists because somebody with an account invited it. Which leaves the
obvious hole — **a fresh deployment has no accounts, so nobody can invite the
first one.** Invite-only is a closed loop until something opens it from outside,
and that something has to be an operator with shell access rather than an
endpoint, because an endpoint that mints the first owner is an endpoint an
attacker races you to.

So: a command, run once, by whoever installs this.

It does not set a password. It provisions the workspace and issues the same
single-use invitation token the invite flow issues, and prints the link. That
keeps one path into the product rather than two, and means the operator running
this never learns the owner's credential.
"""

from __future__ import annotations

from app.bootstrap.first_account import BootstrapResult, create_first_account

__all__ = ["BootstrapResult", "create_first_account"]
