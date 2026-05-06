"""Enterprise inner-API client for the /apps/permitted route.

Wraps `POST /inner/api/webapp/permitted-apps` (defined in ee-2). Until
ee-2 ships the endpoint, every call surfaces 503 from the dify-api side.
This isolates the wire-up so the route + scope + query model can ship
ahead of the cross-repo dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from werkzeug.exceptions import ServiceUnavailable


@dataclass(frozen=True, slots=True)
class PermittedAppRow:
    app_id: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class PermittedAppsPage:
    data: list[PermittedAppRow]
    total: int
    has_more: bool


def list_permitted_apps(
    *,
    subject_email: str,
    subject_issuer: str,
    page: int,
    limit: int,
    mode: str | None = None,
    name: str | None = None,
) -> PermittedAppsPage:
    """Cross-tenant allow-list query for `dfoe_` discovery.

    TODO(ee-2): wire to `POST /inner/api/webapp/permitted-apps`. Until then
    every call returns 503 to keep CLI-side work unblocked behind a stable
    server contract.
    """
    raise ServiceUnavailable("permitted_apps_unavailable")
