"""Task Lambda: task status management and querying."""
import json
import os
from typing import Any, Optional

import boto3

from shared.logger import get_logger, LogContext
from shared.models import api_response, error_response, TASK_STATUSES, user_pk, content_sk
from shared.dynamodb import get_user_tasks, get_task_by_id
from shared.s3_client import get_presigned_download_url

logger = get_logger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

_TABLE_NAME = os.environ.get("TABLE_NAME", "")


def _batch_get_content(user_id: str, content_ids: list[str]) -> dict[str, dict]:
    """Batch fetch content records by content_id for a given user.
    Returns a dict mapping content_id -> content data dict."""
    if not content_ids:
        return {}
    dynamodb = boto3.resource("dynamodb")
    keys = [{"PK": user_pk(user_id), "SK": content_sk(cid)} for cid in content_ids]
    response = dynamodb.batch_get_item(RequestItems={_TABLE_NAME: {"Keys": keys}})
    result = {}
    for item in response.get("Responses", {}).get(_TABLE_NAME, []):
        data = item.get("data", {})
        # content_id is in SK (CONTENT#{id}), not in data
        sk = item.get("SK", "")
        cid = sk.split("#", 1)[1] if "#" in sk else data.get("content_id")
        if cid:
            result[cid] = data
    return result


def lambda_handler(event: dict, context: Any) -> dict:
    request_id = event.get("requestContext", {}).get("requestId", "")
    user_id = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
             .get("sub", "")
    )
    path = event.get("path", "")
    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters") or {}

    with LogContext(logger, request_id=request_id, user_id=user_id):
        if not user_id:
            return error_response(401, "Unauthorized", "UNAUTHORIZED", request_id=request_id)

        task_id = path_params.get("task_id")

        if task_id and method == "GET":
            return _get_task(task_id, user_id, request_id)
        elif method == "GET":
            return _list_tasks(event, user_id, request_id)
        else:
            return error_response(404, "Not found", "NOT_FOUND", request_id=request_id)


def _list_tasks(event: dict, user_id: str, request_id: str) -> dict:
    qs = event.get("queryStringParameters") or {}
    status_filter = qs.get("status")
    page_size = min(int(qs.get("page_size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    next_token = qs.get("next_token")  # base64-encoded LastEvaluatedKey

    # Validate status filter
    if status_filter and status_filter not in TASK_STATUSES:
        return error_response(
            400,
            f"Invalid status filter. Valid values: {sorted(TASK_STATUSES)}",
            "INVALID_STATUS_FILTER",
            request_id=request_id,
        )

    # Decode pagination token
    last_key = None
    if next_token:
        try:
            import base64
            last_key = json.loads(base64.b64decode(next_token).decode())
        except Exception:
            return error_response(400, "Invalid pagination token", "INVALID_TOKEN", request_id=request_id)

    tasks_raw, next_key = get_user_tasks(
        user_id=user_id,
        status_filter=status_filter,
        page_size=page_size,
        last_evaluated_key=last_key,
    )

    # Batch fetch content records for all tasks that have a content_id
    content_ids = [t.get("data", {}).get("content_id") for t in tasks_raw]
    content_ids = list({cid for cid in content_ids if cid})
    content_map = _batch_get_content(user_id, content_ids) if content_ids else {}

    tasks = [_format_task_summary(t, content_map.get(t.get("data", {}).get("content_id"))) for t in tasks_raw]

    # Encode next pagination token
    next_token_out = None
    if next_key:
        import base64
        next_token_out = base64.b64encode(json.dumps(next_key).encode()).decode()

    return api_response(200, {
        "tasks": tasks,
        "count": len(tasks),
        "page_size": page_size,
        "next_token": next_token_out,
    })


def _get_task(task_id: str, user_id: str, request_id: str) -> dict:
    item = get_task_by_id(task_id)
    if not item:
        return error_response(404, "Task not found", "NOT_FOUND", request_id=request_id)

    # Enforce ownership: PK must match the requesting user
    if item.get("PK") != f"USER#{user_id}":
        return error_response(403, "Access denied", "FORBIDDEN", request_id=request_id)

    content_id = item.get("data", {}).get("content_id")
    content_data = _batch_get_content(user_id, [content_id]).get(content_id) if content_id else None

    return api_response(200, _format_task_detail(item, content_data))


def _format_task_summary(item: dict, content_data: Optional[dict] = None) -> dict:
    data = item.get("data", {})
    sk = item.get("SK", "")
    # SK: TASK#{created_at}#{task_id}
    parts = sk.split("#", 2)
    task_id = parts[2] if len(parts) > 2 else ""

    result = {
        "task_id": task_id,
        "task_type": data.get("task_type"),
        "modality": data.get("modality"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "result_summary": data.get("result_summary"),
        "filename": None,
        "file_size": None,
    }
    if content_data:
        result["filename"] = content_data.get("filename")
        result["file_size"] = content_data.get("file_size")
    return result


def _format_task_detail(item: dict, content_data: Optional[dict] = None) -> dict:
    summary = _format_task_summary(item, content_data)
    data = item.get("data", {})
    detail = {
        **summary,
        "content_id": data.get("content_id"),
        "error_message": data.get("error_message"),
        "processing_time_ms": data.get("processing_time_ms"),
        "download_url": None,
    }
    if content_data and content_data.get("s3_key"):
        try:
            detail["download_url"] = get_presigned_download_url(content_data["s3_key"])
        except Exception:
            pass
    return detail
