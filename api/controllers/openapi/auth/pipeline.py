"""Pipeline IS the auth scheme.

`Pipeline.guard(scope=…)` is the only attachment point for endpoints —
that is the design lock-in: forgetting an auth layer is structurally
impossible because there is no "sometimes wrap, sometimes don't" choice.
"""

from __future__ import annotations

from functools import wraps

from flask import request

from controllers.openapi.auth.context import Context, Step
from libs.oauth_bearer import Scope


class Pipeline:
    def __init__(self, *steps: Step) -> None:
        self._steps = steps

    def run(self, ctx: Context) -> None:
        for step in self._steps:
            step(ctx)

    def guard(self, *, scope: Scope):
        def decorator(view):
            @wraps(view)
            def decorated(*args, **kwargs):
                ctx = Context(request=request, required_scope=scope)
                self.run(ctx)
                kwargs.update(
                    app_model=ctx.app,
                    caller=ctx.caller,
                    caller_kind=ctx.caller_kind,
                )
                return view(*args, **kwargs)

            return decorated

        return decorator
