"""Data models and validation for the multimodal retrieval application."""
from dataclasses import dataclass, field
from typing import Optional
import re

# ============================================================
# Constants
# ============================================================

SUPPORTED_MIME_TYPES: dict[str, str] = {
    # Images
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    # Audio
    "audio/mpeg": "audio",
    "audio/wav": "audio",
    "audio/ogg": "audio",
    "audio/x-wav": "audio",
    "audio/mp3": "audio",
    "audio/x-m4a": "audio",
    "audio/m4a": "audio",
    "audio/aac": "audio",
    "audio/flac": "audio",
    "audio/x-flac": "audio",
    "audio/webm": "audio",
    # Video
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/x-matroska": "video",
    "video/webm": "video",
    "video/x-flv": "video",
    "video/mpeg": "video",
    "video/mpg": "video",
    "video/x-ms-wmv": "video",
    "video/3gpp": "video",
    # Documents
    "application/pdf": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "text/plain": "document",
}

# File size limits per modality (bytes), based on Nova MME async API limits
SIZE_LIMITS: dict[str, int] = {
    "image": 50 * 1024 * 1024,         # 50MB
    "audio": 1 * 1024 * 1024 * 1024,   # 1GB
    "video": 2 * 1024 * 1024 * 1024,   # 2GB
    "document": 634 * 1024 * 1024,     # 634MB
}

MAX_TEXT_CHARS = 50_000

# Threshold for sync vs async Bedrock API:
# Audio/Video: ≤30 seconds AND ≤100MB → sync; otherwise async
SYNC_AUDIO_VIDEO_MAX_BYTES = 100 * 1024 * 1024  # 100MB
# For images: ≤25MB inline, ≤50MB via S3 URI → always sync
SYNC_IMAGE_MAX_INLINE_BYTES = 25 * 1024 * 1024   # 25MB inline
SYNC_IMAGE_MAX_S3_BYTES = 50 * 1024 * 1024       # 50MB via S3

TASK_STATUSES = frozenset({"pending", "processing", "completed", "failed"})
TASK_TYPES = frozenset({"upload", "search"})
MODALITIES = frozenset({"text", "image", "audio", "video", "document"})

# ============================================================
# Validation helpers
# ============================================================

class ValidationError(Exception):
    """Input validation error returned to the caller as HTTP 400."""
    def __init__(self, message: str, error_code: str = "VALIDATION_ERROR", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


def detect_modality(mime_type: str) -> str:
    """Return the modality string for a MIME type, or raise ValidationError."""
    modality = SUPPORTED_MIME_TYPES.get(mime_type.lower())
    if not modality:
        raise ValidationError(
            f"Unsupported file format: {mime_type}. Supported formats: "
            + ", ".join(sorted(SUPPORTED_MIME_TYPES.keys())),
            error_code="UNSUPPORTED_FILE_FORMAT",
            details={"supported_mime_types": list(SUPPORTED_MIME_TYPES.keys())},
        )
    return modality


def validate_file_size(modality: str, size_bytes: int) -> None:
    """Raise ValidationError if file exceeds the modality-specific size limit."""
    limit = SIZE_LIMITS.get(modality)
    if limit is None:
        return  # Text modality uses character limit, not byte limit
    if size_bytes > limit:
        raise ValidationError(
            f"File size {size_bytes} bytes exceeds the {modality} limit of {limit} bytes "
            f"({limit // (1024 * 1024)} MB).",
            error_code="FILE_TOO_LARGE",
            details={"modality": modality, "limit_bytes": limit, "actual_bytes": size_bytes},
        )


def validate_text_length(text: str) -> None:
    """Raise ValidationError if text exceeds the character limit."""
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(
            f"Text length {len(text)} characters exceeds the limit of {MAX_TEXT_CHARS} characters.",
            error_code="TEXT_TOO_LONG",
            details={"limit_chars": MAX_TEXT_CHARS, "actual_chars": len(text)},
        )


def should_use_async_api(modality: str, file_size_bytes: int, duration_seconds: Optional[float] = None) -> bool:
    """Determine whether to use Bedrock async API based on modality and file size."""
    if modality == "text":
        return False  # Text is always sync (character limit enforced separately)
    if modality == "image":
        return False  # Images are always sync (≤50MB limit enforced)
    if modality in ("audio", "video"):
        # Use async if file is large OR duration exceeds 30 seconds
        if file_size_bytes > SYNC_AUDIO_VIDEO_MAX_BYTES:
            return True
        if duration_seconds is not None and duration_seconds > 30:
            return True
        return False
    if modality == "document":
        # Documents > 50KB benefit from async (arbitrary threshold for large docs)
        return file_size_bytes > SYNC_AUDIO_VIDEO_MAX_BYTES
    return False


# ============================================================
# Response builders
# ============================================================

def api_response(status_code: int, body: dict, headers: Optional[dict] = None) -> dict:
    """Build an API Gateway Lambda proxy response."""
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    }
    if headers:
        default_headers.update(headers)
    import json
    from decimal import Decimal

    def _json_default(obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj == obj.to_integral_value() else float(obj)
        return str(obj)

    return {
        "statusCode": status_code,
        "headers": default_headers,
        "body": json.dumps(body, default=_json_default),
    }


def error_response(status_code: int, message: str, error_code: str,
                   details: Optional[dict] = None, request_id: str = "") -> dict:
    """Build a standardised error response (never exposes stack traces)."""
    body = {
        "error_code": error_code,
        "message": message,
        "request_id": request_id,
    }
    if details:
        body["details"] = details
    return api_response(status_code, body)


# ============================================================
# DynamoDB key helpers
# ============================================================

def user_pk(user_id: str) -> str:
    return f"USER#{user_id}"

def content_sk(content_id: str) -> str:
    return f"CONTENT#{content_id}"

def task_sk(created_at: str, task_id: str) -> str:
    return f"TASK#{created_at}#{task_id}"

def content_pk(content_id: str) -> str:
    return f"CONTENT#{content_id}"

def task_pk(task_id: str) -> str:
    return f"TASK#{task_id}"


# ============================================================
# Dataclasses for internal use
# ============================================================

@dataclass
class ContentItem:
    content_id: str
    user_id: str
    modality: str
    filename: str
    file_size: int
    mime_type: str
    s3_key: str
    s3_bucket: str
    created_at: str
    is_indexed: bool = False
    metadata: dict = field(default_factory=dict)

    def to_ddb_item(self) -> dict:
        return {
            "PK": user_pk(self.user_id),
            "SK": content_sk(self.content_id),
            "GSI1PK": content_pk(self.content_id),
            "GSI1SK": "METADATA",
            "entity_type": "CONTENT",
            "data": {
                "modality": self.modality,
                "filename": self.filename,
                "file_size": self.file_size,
                "mime_type": self.mime_type,
                "s3_key": self.s3_key,
                "s3_bucket": self.s3_bucket,
                "is_indexed": self.is_indexed,
                "created_at": self.created_at,
                "metadata": self.metadata,
            },
        }


@dataclass
class TaskItem:
    task_id: str
    user_id: str
    task_type: str      # "upload" | "search"
    modality: str
    status: str         # "pending" | "processing" | "completed" | "failed"
    created_at: str
    updated_at: str
    content_id: Optional[str] = None
    error_message: Optional[str] = None
    processing_time_ms: Optional[int] = None
    result_summary: Optional[str] = None
    async_invocation_arn: Optional[str] = None  # Bedrock async job ARN

    def to_ddb_item(self) -> dict:
        data: dict = {
            "task_type": self.task_type,
            "modality": self.modality,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.content_id:
            data["content_id"] = self.content_id
        if self.error_message:
            data["error_message"] = self.error_message
        if self.processing_time_ms is not None:
            data["processing_time_ms"] = self.processing_time_ms
        if self.result_summary:
            data["result_summary"] = self.result_summary
        if self.async_invocation_arn:
            data["async_invocation_arn"] = self.async_invocation_arn
        return {
            "PK": user_pk(self.user_id),
            "SK": task_sk(self.created_at, self.task_id),
            "GSI1PK": task_pk(self.task_id),
            "GSI1SK": "DETAIL",
            "entity_type": "TASK",
            "data": data,
        }
