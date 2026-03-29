"""DynamoDB operations for the single-table design."""
import os
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr

from shared.models import (
    ContentItem, TaskItem,
    user_pk, content_sk, task_sk, content_pk, task_pk,
    TASK_STATUSES,
)
from shared.logger import get_logger

logger = get_logger(__name__)

_TABLE_NAME = os.environ.get("TABLE_NAME", "")


def _table():
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(_TABLE_NAME)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ============================================================
# Content operations
# ============================================================

def put_content(item: ContentItem) -> None:
    _table().put_item(Item=item.to_ddb_item())
    logger.info("Content created", extra={"content_id": item.content_id, "modality": item.modality})


def get_content_by_id(content_id: str) -> Optional[dict]:
    """Fetch content metadata by content_id using GSI1."""
    response = _table().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(content_pk(content_id)) & Key("GSI1SK").eq("METADATA"),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def get_user_contents(user_id: str, limit: int = 100) -> list[dict]:
    response = _table().query(
        KeyConditionExpression=Key("PK").eq(user_pk(user_id)) & Key("SK").begins_with("CONTENT#"),
        Limit=limit,
        ScanIndexForward=False,
    )
    return response.get("Items", [])


def mark_content_indexed(content_id: str, user_id: str) -> None:
    """Set is_indexed=True on a content item."""
    # First get the item to find SK
    content = get_content_by_id(content_id)
    if not content:
        logger.warning("Content not found for indexing", extra={"content_id": content_id})
        return
    _table().update_item(
        Key={"PK": user_pk(user_id), "SK": content_sk(content_id)},
        UpdateExpression="SET #data.#is_indexed = :true",
        ExpressionAttributeNames={"#data": "data", "#is_indexed": "is_indexed"},
        ExpressionAttributeValues={":true": True},
    )


# ============================================================
# Task operations
# ============================================================

def put_task(item: TaskItem) -> None:
    _table().put_item(Item=item.to_ddb_item())
    logger.info("Task created", extra={"task_id": item.task_id, "task_type": item.task_type})


def get_task_by_id(task_id: str) -> Optional[dict]:
    """Fetch task by task_id using GSI1."""
    response = _table().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(task_pk(task_id)) & Key("GSI1SK").eq("DETAIL"),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def get_user_tasks(
    user_id: str,
    status_filter: Optional[str] = None,
    page_size: int = 20,
    last_evaluated_key: Optional[dict] = None,
) -> tuple[list[dict], Optional[dict]]:
    """Return tasks for a user, newest first. Supports status filtering and pagination."""
    kwargs: dict = {
        "KeyConditionExpression": Key("PK").eq(user_pk(user_id)) & Key("SK").begins_with("TASK#"),
        "Limit": page_size,
        "ScanIndexForward": False,  # newest first
    }
    if status_filter and status_filter in TASK_STATUSES:
        kwargs["FilterExpression"] = Attr("data.status").eq(status_filter)
    if last_evaluated_key:
        kwargs["ExclusiveStartKey"] = last_evaluated_key

    response = _table().query(**kwargs)
    return response.get("Items", []), response.get("LastEvaluatedKey")


def update_task_status(
    task_id: str,
    user_id: str,
    created_at: str,
    status: str,
    error_message: Optional[str] = None,
    processing_time_ms: Optional[int] = None,
    result_summary: Optional[str] = None,
    async_invocation_arn: Optional[str] = None,
    segment_duration_seconds: Optional[int] = None,
) -> None:
    """Update task status and optional fields atomically."""
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    update_parts = ["SET #d.#status = :status, #d.#updated_at = :updated_at"]
    expr_names = {"#d": "data", "#status": "status", "#updated_at": "updated_at"}
    expr_values: dict = {":status": status, ":updated_at": now_iso()}

    if error_message is not None:
        update_parts.append("#d.#error_message = :error")
        expr_names["#error_message"] = "error_message"
        expr_values[":error"] = error_message

    if processing_time_ms is not None:
        update_parts.append("#d.#proc_time = :proc_time")
        expr_names["#proc_time"] = "processing_time_ms"
        expr_values[":proc_time"] = processing_time_ms

    if result_summary is not None:
        update_parts.append("#d.#summary = :summary")
        expr_names["#summary"] = "result_summary"
        expr_values[":summary"] = result_summary

    if async_invocation_arn is not None:
        update_parts.append("#d.#arn = :arn")
        expr_names["#arn"] = "async_invocation_arn"
        expr_values[":arn"] = async_invocation_arn

    if segment_duration_seconds is not None:
        update_parts.append("#d.#seg_dur = :seg_dur")
        expr_names["#seg_dur"] = "segment_duration_seconds"
        expr_values[":seg_dur"] = segment_duration_seconds

    update_expr = ", ".join(update_parts)

    _table().update_item(
        Key={"PK": user_pk(user_id), "SK": task_sk(created_at, task_id)},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
    logger.info("Task status updated", extra={"task_id": task_id, "status": status})


def get_pending_async_tasks(limit: int = 50) -> list[dict]:
    """Scan for tasks with status=processing and an async_invocation_arn set.
    Used by the Embedding Poller Lambda.
    Note: This uses a Scan which is acceptable for a low-volume poller.
    In production, consider a GSI on status for efficiency.
    """
    response = _table().scan(
        FilterExpression=(
            Attr("entity_type").eq("TASK") &
            Attr("data.status").eq("processing") &
            Attr("data.async_invocation_arn").exists()
        ),
        Limit=limit,
    )
    return response.get("Items", [])


# ============================================================
# Embedding metadata
# ============================================================

def put_embedding_metadata(content_id: str, model_id: str, vector_dimension: int,
                           s3_vectors_key: str) -> None:
    """Store embedding metadata in DynamoDB."""
    _table().put_item(Item={
        "PK": content_pk(content_id),
        "SK": "EMBEDDING",
        "entity_type": "EMBEDDING",
        "data": {
            "model_id": model_id,
            "vector_dimension": vector_dimension,
            "s3_vectors_key": s3_vectors_key,
            "created_at": now_iso(),
        },
    })


def get_embedding_metadata(content_id: str) -> Optional[dict]:
    response = _table().get_item(Key={"PK": content_pk(content_id), "SK": "EMBEDDING"})
    return response.get("Item")


# ============================================================
# Transcription metadata
# ============================================================

def update_content_transcribe_status(
    content_id: str,
    user_id: str,
    transcribe_status: str,
    transcribe_job_name: Optional[str] = None,
    transcript: Optional[str] = None,
) -> None:
    """Update transcription status and optional transcript text on a content item."""
    update_parts = ["SET #d.#ts = :ts"]
    expr_names = {"#d": "data", "#ts": "transcribe_status"}
    expr_values: dict = {":ts": transcribe_status}

    if transcribe_job_name is not None:
        update_parts.append("#d.#tjn = :tjn")
        expr_names["#tjn"] = "transcribe_job_name"
        expr_values[":tjn"] = transcribe_job_name

    if transcript is not None:
        update_parts.append("#d.#tr = :tr")
        expr_names["#tr"] = "transcript"
        expr_values[":tr"] = transcript

    update_expr = ", ".join(update_parts)
    _table().update_item(
        Key={"PK": user_pk(user_id), "SK": content_sk(content_id)},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )
    logger.info("Content transcribe status updated",
                extra={"content_id": content_id, "transcribe_status": transcribe_status})


def get_pending_transcribe_content(limit: int = 50) -> list[dict]:
    """Scan for content records where transcribe_status='pending'.
    Used by the Transcribe Poller Lambda.
    """
    response = _table().scan(
        FilterExpression=(
            Attr("entity_type").eq("CONTENT") &
            Attr("data.transcribe_status").eq("pending")
        ),
        Limit=limit,
    )
    return response.get("Items", [])
