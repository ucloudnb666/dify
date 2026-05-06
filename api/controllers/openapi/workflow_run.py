"""POST /openapi/v1/apps/<app_id>/workflows/run — port of
service_api/app/workflow.py:WorkflowRunApi."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, ValidationError
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound

from controllers.common.controller_schemas import WorkflowRunPayload as WorkflowRunPayloadBase
from controllers.openapi import openapi_ns
from controllers.openapi._audit import emit_app_run
from controllers.openapi.auth.composition import OAUTH_BEARER_PIPELINE
from controllers.service_api.app.error import (
    CompletionRequestError,
    NotWorkflowAppError,
    ProviderModelCurrentlyNotSupportError,
    ProviderNotInitializeError,
    ProviderQuotaExceededError,
)
from controllers.web.error import InvokeRateLimitError as InvokeRateLimitHttpError
from core.app.entities.app_invoke_entities import InvokeFrom
from core.errors.error import (
    ModelCurrentlyNotSupportError,
    ProviderTokenNotInitError,
    QuotaExceededError,
)
from graphon.model_runtime.errors.invoke import InvokeError
from libs import helper
from libs.oauth_bearer import Scope
from models.model import App, AppMode
from services.app_generate_service import AppGenerateService
from services.errors.app import (
    IsDraftWorkflowError,
    WorkflowIdFormatError,
    WorkflowNotFoundError,
)
from services.errors.llm import InvokeRateLimitError

logger = logging.getLogger(__name__)


class WorkflowRunRequest(WorkflowRunPayloadBase):
    response_mode: Literal["blocking", "streaming"] | None = None


class WorkflowRunData(BaseModel):
    id: str
    workflow_id: str
    status: str
    outputs: dict[str, Any] = {}
    error: str | None = None
    elapsed_time: float | None = None
    total_tokens: int | None = None
    total_steps: int | None = None
    created_at: int | None = None
    finished_at: int | None = None


class WorkflowRunResponse(BaseModel):
    workflow_run_id: str
    task_id: str
    data: WorkflowRunData


def _unpack_app(app_model):
    return app_model


def _unpack_caller(caller):
    return caller


@openapi_ns.route("/apps/<string:app_id>/workflows/run")
class WorkflowRunApi(Resource):
    @OAUTH_BEARER_PIPELINE.guard(scope=Scope.APPS_RUN)
    def post(self, app_id: str, app_model: App, caller, caller_kind: str):
        app = _unpack_app(app_model)
        if AppMode.value_of(app.mode) != AppMode.WORKFLOW:
            raise NotWorkflowAppError()

        body = request.get_json(silent=True) or {}
        body.pop("user", None)
        try:
            payload = WorkflowRunRequest.model_validate(body)
        except ValidationError as exc:
            raise BadRequest(str(exc))
        args = payload.model_dump(exclude_none=True)
        streaming = payload.response_mode == "streaming"

        try:
            response = AppGenerateService.generate(
                app_model=app,
                user=_unpack_caller(caller),
                args=args,
                invoke_from=InvokeFrom.OPENAPI,
                streaming=streaming,
            )
        except WorkflowNotFoundError as ex:
            raise NotFound(str(ex))
        except (IsDraftWorkflowError, WorkflowIdFormatError) as ex:
            raise BadRequest(str(ex))
        except ProviderTokenNotInitError as ex:
            raise ProviderNotInitializeError(ex.description)
        except QuotaExceededError:
            raise ProviderQuotaExceededError()
        except ModelCurrentlyNotSupportError:
            raise ProviderModelCurrentlyNotSupportError()
        except InvokeRateLimitError as ex:
            raise InvokeRateLimitHttpError(ex.description)
        except InvokeError as e:
            raise CompletionRequestError(e.description)
        except ValueError:
            raise
        except Exception:
            logger.exception("internal server error.")
            raise InternalServerError()

        emit_app_run(
            app_id=app.id,
            tenant_id=app.tenant_id,
            caller_kind=caller_kind,
            mode=str(app.mode),
        )

        if streaming:
            return helper.compact_generate_response(response)

        if isinstance(response, tuple):
            body_dict: Any = response[0]  # pyright: ignore[reportArgumentType]
        else:
            body_dict = response
        if not isinstance(body_dict, Mapping):
            raise InternalServerError("blocking generate returned non-mapping response")
        return WorkflowRunResponse.model_validate(dict(body_dict)).model_dump(mode="json"), 200
