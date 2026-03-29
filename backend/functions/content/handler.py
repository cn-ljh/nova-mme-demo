"""Content Lambda: file upload management, presigned URLs, metadata retrieval."""
import base64
import json
import os
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.logger import get_logger, LogContext
from shared.models import (
    api_response, error_response, ValidationError,
    detect_modality, validate_file_size, validate_text_length,
    ContentItem, TaskItem, should_use_async_api,
    SUPPORTED_MIME_TYPES, SIZE_LIMITS, MAX_TEXT_CHARS,
)
from shared.dynamodb import put_content, put_task, get_content_by_id, now_iso
from shared.s3_client import (
    generate_presigned_upload_url, upload_bytes,
    build_content_s3_key, build_s3_uri, get_presigned_download_url,
    generate_cloudfront_signed_url, get_cloudfront_private_key,
    CONTENT_BUCKET,
)

logger = get_logger(__name__)

EMBEDDING_QUEUE_URL = os.environ.get("EMBEDDING_QUEUE_URL", "")
LARGE_FILE_EMBEDDING_QUEUE_URL = os.environ.get("LARGE_FILE_EMBEDDING_QUEUE_URL", "")
SMALL_FILE_THRESHOLD = int(os.environ.get("SMALL_FILE_THRESHOLD_BYTES", str(10 * 1024 * 1024)))  # 10MB

_cloudfront_private_key: str | None = None  # module-level cache


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

    with LogContext(logger, request_id=request_id, user_id=user_id):
        if not user_id:
            return error_response(401, "Unauthorized", "UNAUTHORIZED", request_id=request_id)

        if path.endswith("/request-upload") and method == "POST":
            return _request_upload(event, user_id, request_id)
        elif path.endswith("/confirm-upload") and method == "POST":
            return _confirm_upload(event, user_id, request_id)
        elif path.endswith("/upload-text") and method == "POST":
            return _upload_text(event, user_id, request_id)
        elif path.endswith("/query-upload") and method == "POST":
            return _query_upload(event, user_id, request_id)
        elif "/download" in path and method == "GET":
            content_id = event.get("pathParameters", {}).get("content_id", "")
            return _get_download_url(content_id, user_id, request_id)
        elif "/content/" in path and method == "GET":
            content_id = event.get("pathParameters", {}).get("content_id", "")
            return _get_content(content_id, user_id, request_id)
        else:
            return error_response(404, "Not found", "NOT_FOUND", request_id=request_id)


def _request_upload(event: dict, user_id: str, request_id: str) -> dict:
    """Step 1: Generate a presigned S3 POST URL for the client to upload directly."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    filename = body.get("filename", "").strip()
    mime_type = body.get("mime_type", "").strip()
    file_size = int(body.get("file_size", 0))

    if not filename or not mime_type or not file_size:
        return error_response(400, "filename, mime_type, and file_size are required", "MISSING_FIELDS", request_id=request_id)

    try:
        modality = detect_modality(mime_type)
        validate_file_size(modality, file_size)
    except ValidationError as exc:
        return error_response(400, exc.message, exc.error_code, details=exc.details, request_id=request_id)

    content_id = str(uuid.uuid4())
    s3_key = build_content_s3_key(user_id, content_id, filename)
    size_limit = SIZE_LIMITS.get(modality, file_size)

    presigned = generate_presigned_upload_url(
        s3_key=s3_key,
        mime_type=mime_type,
        max_size_bytes=size_limit,
        expires_in=3600,
    )

    logger.info("Presigned upload URL generated", extra={"content_id": content_id, "modality": modality})
    return api_response(200, {
        "content_id": content_id,
        "upload_url": presigned["url"],
        "upload_fields": presigned["fields"],
        "s3_key": s3_key,
        "expires_in": 3600,
    })


def _confirm_upload(event: dict, user_id: str, request_id: str) -> dict:
    """Step 2: Called after the client has uploaded to S3. Creates DynamoDB records and enqueues embedding."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    content_id = body.get("content_id", "").strip()
    s3_key = body.get("s3_key", "").strip()
    filename = body.get("filename", "").strip()
    mime_type = body.get("mime_type", "").strip()
    file_size = int(body.get("file_size", 0))

    if not all([content_id, s3_key, filename, mime_type, file_size]):
        return error_response(400, "content_id, s3_key, filename, mime_type, file_size are required", "MISSING_FIELDS", request_id=request_id)

    try:
        modality = detect_modality(mime_type)
        validate_file_size(modality, file_size)
    except ValidationError as exc:
        return error_response(400, exc.message, exc.error_code, details=exc.details, request_id=request_id)

    created_at = now_iso()
    task_id = str(uuid.uuid4())

    content_item = ContentItem(
        content_id=content_id,
        user_id=user_id,
        modality=modality,
        filename=filename,
        file_size=file_size,
        mime_type=mime_type,
        s3_key=s3_key,
        s3_bucket=CONTENT_BUCKET,
        created_at=created_at,
    )
    task_item = TaskItem(
        task_id=task_id,
        user_id=user_id,
        task_type="upload",
        modality=modality,
        status="pending",
        created_at=created_at,
        updated_at=created_at,
        content_id=content_id,
    )

    put_content(content_item)
    put_task(task_item)

    _enqueue_embedding(
        content_id=content_id,
        s3_key=s3_key,
        modality=modality,
        task_id=task_id,
        user_id=user_id,
        file_size=file_size,
        created_at=created_at,
        mime_type=mime_type,
    )

    logger.info("Upload confirmed, embedding queued", extra={"task_id": task_id, "content_id": content_id})
    return api_response(201, {
        "task_id": task_id,
        "content_id": content_id,
        "modality": modality,
        "status": "pending",
    })


def _upload_text(event: dict, user_id: str, request_id: str) -> dict:
    """Upload text content directly (no file, just a string)."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    text = body.get("text", "")
    title = body.get("title", "Untitled text")

    if not text:
        return error_response(400, "text is required", "MISSING_FIELDS", request_id=request_id)

    try:
        validate_text_length(text)
    except ValidationError as exc:
        return error_response(400, exc.message, exc.error_code, details=exc.details, request_id=request_id)

    created_at = now_iso()
    content_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    filename = f"{title[:50]}.txt"
    s3_key = build_content_s3_key(user_id, content_id, filename)

    # Upload text to S3 for reference
    upload_bytes(s3_key, text.encode("utf-8"), "text/plain")

    content_item = ContentItem(
        content_id=content_id,
        user_id=user_id,
        modality="text",
        filename=filename,
        file_size=len(text.encode("utf-8")),
        mime_type="text/plain",
        s3_key=s3_key,
        s3_bucket=CONTENT_BUCKET,
        created_at=created_at,
        metadata={"title": title, "char_count": len(text)},
    )
    task_item = TaskItem(
        task_id=task_id,
        user_id=user_id,
        task_type="upload",
        modality="text",
        status="pending",
        created_at=created_at,
        updated_at=created_at,
        content_id=content_id,
    )

    put_content(content_item)
    put_task(task_item)

    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=EMBEDDING_QUEUE_URL,
        MessageBody=json.dumps({
            "content_id": content_id,
            "s3_key": s3_key,
            "s3_bucket": CONTENT_BUCKET,
            "modality": "text",
            "task_id": task_id,
            "user_id": user_id,
            "file_size": len(text.encode("utf-8")),
            "created_at": created_at,
            "text_content": text,  # inline for text (small)
        }),
    )

    logger.info("Text upload created", extra={"task_id": task_id, "content_id": content_id})
    return api_response(201, {
        "task_id": task_id,
        "content_id": content_id,
        "modality": "text",
        "status": "pending",
    })


def _get_content(content_id: str, user_id: str, request_id: str) -> dict:
    if not content_id:
        return error_response(400, "content_id is required", "MISSING_FIELDS", request_id=request_id)

    item = get_content_by_id(content_id)
    if not item:
        return error_response(404, "Content not found", "NOT_FOUND", request_id=request_id)

    # Enforce ownership
    if item.get("PK") != f"USER#{user_id}":
        return error_response(403, "Access denied", "FORBIDDEN", request_id=request_id)

    data = item.get("data", {})
    return api_response(200, {
        "content_id": content_id,
        "user_id": user_id,
        "modality": data.get("modality"),
        "filename": data.get("filename"),
        "file_size": data.get("file_size"),
        "mime_type": data.get("mime_type"),
        "s3_key": data.get("s3_key"),
        "s3_bucket": data.get("s3_bucket"),
        "is_indexed": data.get("is_indexed", False),
        "created_at": data.get("created_at"),
        "metadata": data.get("metadata", {}),
    })


def _get_download_url(content_id: str, user_id: str, request_id: str) -> dict:
    if not content_id:
        return error_response(400, "content_id is required", "MISSING_FIELDS", request_id=request_id)

    item = get_content_by_id(content_id)
    if not item:
        return error_response(404, "Content not found", "NOT_FOUND", request_id=request_id)

    if item.get("PK") != f"USER#{user_id}":
        return error_response(403, "Access denied", "FORBIDDEN", request_id=request_id)

    data = item.get("data", {})
    s3_key = data.get("s3_key", "")
    modality = data.get("modality", "")

    # Use longer expiry for large media files
    expires_in = 14400 if modality in ("video", "audio") else 3600

    global _cloudfront_private_key
    if _cloudfront_private_key is None:
        _cloudfront_private_key = get_cloudfront_private_key()

    url = generate_cloudfront_signed_url(
        s3_key=s3_key,
        expires_in_seconds=expires_in,
        private_key_pem=_cloudfront_private_key,
    )

    return api_response(200, {"download_url": url, "expires_in": expires_in})


def _query_upload(event: dict, user_id: str, request_id: str) -> dict:
    """Generate a presigned S3 POST URL for uploading a large query file (avoids 413 on search)."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    filename = body.get("filename", "").strip()
    mime_type = body.get("mime_type", "").strip()
    file_size = int(body.get("file_size", 0))

    if not filename or not mime_type or not file_size:
        return error_response(400, "filename, mime_type, and file_size are required", "MISSING_FIELDS", request_id=request_id)

    try:
        modality = detect_modality(mime_type)
        validate_file_size(modality, file_size)
    except ValidationError as exc:
        return error_response(400, exc.message, exc.error_code, details=exc.details, request_id=request_id)

    ext = os.path.splitext(filename)[1]  # e.g. ".mp3"
    s3_key = f"tmp/query/{user_id}/{uuid.uuid4()}{ext}"

    presigned = generate_presigned_upload_url(
        s3_key=s3_key,
        mime_type=mime_type,
        max_size_bytes=file_size,
        expires_in=900,
    )

    logger.info("Query upload presigned URL generated", extra={"s3_key": s3_key})
    return api_response(200, {
        "upload_url": presigned["url"],
        "upload_fields": presigned["fields"],
        "s3_key": s3_key,
    })


def _enqueue_embedding(
    content_id: str, s3_key: str, modality: str,
    task_id: str, user_id: str, file_size: int, created_at: str,
    mime_type: str = "",
) -> None:
    """Route the embedding task to the appropriate SQS queue based on file size."""
    use_async = should_use_async_api(modality, file_size)
    queue_url = LARGE_FILE_EMBEDDING_QUEUE_URL if use_async else EMBEDDING_QUEUE_URL

    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({
            "content_id": content_id,
            "s3_key": s3_key,
            "s3_bucket": CONTENT_BUCKET,
            "modality": modality,
            "task_id": task_id,
            "user_id": user_id,
            "file_size": file_size,
            "created_at": created_at,
            "mime_type": mime_type,
            "use_async": use_async,
        }),
    )
    logger.info(
        f"Queued embedding task (async={use_async})",
        extra={"task_id": task_id, "queue": "large" if use_async else "small"},
    )
