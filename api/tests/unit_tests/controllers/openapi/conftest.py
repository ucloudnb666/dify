import pytest

from controllers.openapi.auth.pipeline import Pipeline


@pytest.fixture
def bypass_pipeline(monkeypatch):
    """Stub Pipeline.run so endpoint decoration does not invoke real auth.

    Module-level @OAUTH_BEARER_PIPELINE.guard(...) captures the real
    pipeline at import time; mocking the module attribute does not undo
    that. Patching Pipeline.run on the class is the bypass that actually
    works.
    """
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: None)
