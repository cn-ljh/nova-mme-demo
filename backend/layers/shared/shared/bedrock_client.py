"""Amazon Bedrock Nova MME integration with retry logic."""
import base64
import json
import os
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from shared.logger import get_logger
from shared.models import should_use_async_api

logger = get_logger(__name__)

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-2-multimodal-embeddings-v1:0")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
EMBEDDING_DIM = 1024

# Retry configuration
MAX_RETRIES = 3
INITIAL_DELAY = 1.0
BACKOFF_FACTOR = 2.0
MAX_DELAY = 30.0
RETRYABLE_ERRORS = frozenset({
    "ThrottlingException",
    "ServiceUnavailableException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
})


def _bedrock_runtime_client():
    return boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


def _with_retry(fn, *args, **kwargs):
    """Call fn with exponential backoff retry on throttling/transient errors."""
    delay = INITIAL_DELAY
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in RETRYABLE_ERRORS and attempt < MAX_RETRIES:
                wait = min(delay, MAX_DELAY)
                logger.warning(
                    f"Bedrock API error {error_code}, retrying in {wait:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                delay *= BACKOFF_FACTOR
                last_exc = exc
            else:
                raise
        except Exception as exc:
            # Network errors are also retryable
            if attempt < MAX_RETRIES:
                wait = min(delay, MAX_DELAY)
                logger.warning(f"Transient error, retrying in {wait:.1f}s: {exc}")
                time.sleep(wait)
                delay *= BACKOFF_FACTOR
                last_exc = exc
            else:
                raise
    raise last_exc  # unreachable but satisfies type checkers


# ============================================================
# Response parsing
# ============================================================

def _extract_embedding(result: dict) -> list[float]:
    """Extract embedding vector from a Nova MME InvokeModel response.

    Nova MME returns: {"embeddings": [{"embedding": [...], ...}]}
    """
    embeddings = result.get("embeddings")
    if embeddings and isinstance(embeddings, list):
        first = embeddings[0]
        if isinstance(first, dict):
            return first["embedding"]
        if isinstance(first, list):
            return first
    raise ValueError(f"Cannot extract embedding from response keys: {list(result.keys())}")


def _normalise_format(mime_type: Optional[str], s3_uri: str, default: str) -> str:
    """Derive a file format string from mime_type or s3_uri extension."""
    if mime_type:
        raw = mime_type.split("/")[-1].lower()
    else:
        raw = s3_uri.rsplit(".", 1)[-1].lower() if "." in s3_uri else default
    fmt_map = {
        "jpg": "jpeg",
        "x-wav": "wav",
        "mpeg": "mp3",
        "x-matroska": "mkv",
        "quicktime": "mov",
        "x-flv": "flv",
        "mpg": "mpeg",
        "x-ms-wmv": "wmv",
        "3gpp": "3gp",
        "mp3": "mp3",
        "x-m4a": "mp4",
        "m4a": "mp4",
        "aac": "mp4",
    }
    return fmt_map.get(raw, raw)


# ============================================================
# Sync embedding generation
# ============================================================

def embed_text_sync(text: str, embedding_purpose: str = "GENERIC_INDEX") -> list[float]:
    """Generate embedding for text using sync InvokeModel."""
    client = _bedrock_runtime_client()
    body = {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": embedding_purpose,
            "embeddingDimension": EMBEDDING_DIM,
            "text": {
                "truncationMode": "END",
                "value": text,
            },
        },
    }
    response = _with_retry(
        client.invoke_model,
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return _extract_embedding(result)


def embed_image_sync(image_bytes: bytes, mime_type: str, s3_uri: Optional[str] = None, embedding_purpose: str = "GENERIC_INDEX") -> list[float]:
    """Generate embedding for an image using sync InvokeModel.
    - Small images (≤25MB): send inline as base64.
    - Larger images (≤50MB): reference via s3_uri.
    """
    client = _bedrock_runtime_client()
    fmt = _normalise_format(mime_type, s3_uri or "", "jpeg")

    if s3_uri and len(image_bytes) > 25 * 1024 * 1024:
        source = {"s3Location": {"uri": s3_uri}}
    else:
        source = {"bytes": base64.b64encode(image_bytes).decode()}

    body = {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": embedding_purpose,
            "embeddingDimension": EMBEDDING_DIM,
            "image": {
                "format": fmt,
                "detailLevel": "STANDARD_IMAGE",
                "source": source,
            },
        },
    }
    response = _with_retry(
        client.invoke_model,
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return _extract_embedding(result)


def embed_audio_video_sync(
    s3_uri: str,
    modality: str,
    file_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
    embedding_purpose: str = "GENERIC_INDEX",
) -> list[float]:
    """Generate embedding for small audio/video (≤30s, ≤100MB)."""
    client = _bedrock_runtime_client()
    content_type = "audio" if modality == "audio" else "video"
    default_fmt = "mp3" if modality == "audio" else "mp4"
    fmt = _normalise_format(mime_type, s3_uri, default_fmt)

    if file_bytes:
        source = {"bytes": base64.b64encode(file_bytes).decode()}
    else:
        source = {"s3Location": {"uri": s3_uri}}

    content_item: dict = {"format": fmt, "source": source}
    if modality == "video":
        content_item["embeddingMode"] = "AUDIO_VIDEO_COMBINED"

    body = {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": embedding_purpose,
            "embeddingDimension": EMBEDDING_DIM,
            content_type: content_item,
        },
    }
    response = _with_retry(
        client.invoke_model,
        modelId=MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(response["body"].read())
    return _extract_embedding(result)


# ============================================================
# Async embedding generation (large files)
# ============================================================

def start_async_embed_audio_video(
    s3_uri: str,
    modality: str,
    output_s3_uri: str,
    segment_duration_seconds: int = 10,
    video_mode: str = "AUDIO_VIDEO_COMBINED",
    mime_type: Optional[str] = None,
) -> str:
    """Start an async Bedrock embedding job for large audio/video.
    Returns the invocation ARN.
    """
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    content_type = "audio" if modality == "audio" else "video"
    default_fmt = "mp3" if modality == "audio" else "mp4"
    fmt = _normalise_format(mime_type, s3_uri, default_fmt)

    content_item: dict = {
        "format": fmt,
        "source": {"s3Location": {"uri": s3_uri}},
        "segmentationConfig": {"durationSeconds": segment_duration_seconds},
    }
    if modality == "video":
        content_item["embeddingMode"] = video_mode

    model_input = {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SEGMENTED_EMBEDDING",
        "segmentedEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX",
            "embeddingDimension": EMBEDDING_DIM,
            content_type: content_item,
        },
    }
    response = _with_retry(
        client.start_async_invoke,
        modelId=MODEL_ID,
        modelInput=model_input,
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}},
    )
    arn = response["invocationArn"]
    logger.info(f"Started async Bedrock job", extra={"invocation_arn": arn, "modality": modality})
    return arn


def start_async_embed_document(s3_uri: str, output_s3_uri: str,
                               max_length_chars: int = 32000) -> str:
    """Start an async Bedrock embedding job for large text/document files.
    Returns the invocation ARN.
    """
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    model_input = {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SEGMENTED_EMBEDDING",
        "segmentedEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX",
            "embeddingDimension": EMBEDDING_DIM,
            "text": {
                "truncationMode": "END",
                "source": {"s3Location": {"uri": s3_uri}},
                "segmentationConfig": {"maxLengthChars": max_length_chars},
            },
        },
    }
    response = _with_retry(
        client.start_async_invoke,
        modelId=MODEL_ID,
        modelInput=model_input,
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3_uri}},
    )
    arn = response["invocationArn"]
    logger.info(f"Started async document embedding job", extra={"invocation_arn": arn})
    return arn


def get_async_job_status(invocation_arn: str) -> dict:
    """Poll the status of an async Bedrock invocation.
    Returns dict with 'status' ('InProgress', 'Completed', 'Failed') and optional 'outputDataConfig'.
    """
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    response = client.get_async_invoke(invocationArn=invocation_arn)
    return {
        "status": response["status"],
        "output_s3_uri": response.get("outputDataConfig", {}).get("s3OutputDataConfig", {}).get("s3Uri"),
        "failure_message": response.get("failureMessage"),
    }


# ============================================================
# Unified entry point used by Embedding Lambda
# ============================================================

def generate_embedding_sync(
    modality: str,
    s3_uri: str,
    file_bytes: Optional[bytes] = None,
    text: Optional[str] = None,
    mime_type: Optional[str] = None,
    embedding_purpose: str = "GENERIC_INDEX",
) -> list[float]:
    """Generate a single embedding synchronously. Raises on failure."""
    if modality == "text":
        if text is None:
            raise ValueError("text is required for text modality")
        return embed_text_sync(text, embedding_purpose=embedding_purpose)
    elif modality == "image":
        if file_bytes is None:
            raise ValueError("file_bytes is required for image modality")
        return embed_image_sync(file_bytes, mime_type or "image/jpeg", s3_uri=s3_uri, embedding_purpose=embedding_purpose)
    elif modality in ("audio", "video"):
        return embed_audio_video_sync(s3_uri, modality, file_bytes=file_bytes, mime_type=mime_type, embedding_purpose=embedding_purpose)
    elif modality == "document":
        # Nova MME has no native sync document modality.
        # Callers must extract text first and pass modality="text", or use the async path.
        raise ValueError(
            "Document sync embedding is not supported directly; "
            "extract text and use modality='text', or use async path for PDFs"
        )
    else:
        raise ValueError(f"Unknown modality: {modality}")
