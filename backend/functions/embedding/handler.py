"""Embedding Lambda: process SQS messages and generate Bedrock embeddings.

Two entry points:
- lambda_handler: sync embeddings for small files (triggered by EmbeddingQueue)
- large_file_handler: starts async Bedrock jobs for large files (triggered by LargeFileEmbeddingQueue)
"""
import json
import os
import time
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from shared.logger import get_logger, LogContext
from shared.bedrock_client import (
    generate_embedding_sync,
    start_async_embed_audio_video,
    start_async_embed_document,
)
from shared.s3_client import (
    read_object, read_text_object, build_s3_uri, build_embeddings_output_prefix,
    put_vectors, CONTENT_BUCKET, EMBEDDINGS_OUTPUT_BUCKET,
    VECTOR_BUCKET_NAME, VECTOR_INDEX_NAME,
)
from shared.dynamodb import (
    update_task_status, mark_content_indexed,
    put_embedding_metadata, now_iso, update_content_transcribe_status,
)
from shared.models import SYNC_AUDIO_VIDEO_MAX_BYTES

logger = get_logger(__name__)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-2-multimodal-embeddings-v1:0")
TRANSCRIBE_REGION = os.environ.get("AWS_REGION", "us-west-2")

# Mapping from MIME type to Amazon Transcribe MediaFormat
_TRANSCRIBE_FORMAT_MAP: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "video/mp4": "mp4",
    "video/quicktime": "mp4",
    "video/webm": "webm",
    "video/3gpp": "mp4",
}


def _start_transcribe_job(
    s3_key: str,
    s3_bucket: str,
    content_id: str,
    user_id: str,
    mime_type: Optional[str],
) -> Optional[str]:
    """Start an Amazon Transcribe job for an audio/video file.

    Returns the job name on success, None if the file type is unsupported or on error.
    The transcript JSON will be written to:
        s3://{CONTENT_BUCKET}/transcripts/{user_id}/{content_id}/transcript.json
    """
    media_format = _TRANSCRIBE_FORMAT_MAP.get((mime_type or "").lower())
    if not media_format:
        logger.warning(f"Skipping Transcribe: unsupported mime_type={mime_type}")
        return None

    # Job names must be unique and contain only alphanumerics/hyphens/underscores (max 200 chars)
    job_name = f"mmr-{content_id[:32]}"
    media_uri = build_s3_uri(s3_bucket, s3_key)
    output_key = f"transcripts/{user_id}/{content_id}/transcript.json"

    try:
        client = boto3.client("transcribe", region_name=TRANSCRIBE_REGION)
        client.start_transcription_job(
            TranscriptionJobName=job_name,
            IdentifyLanguage=True,
            Media={"MediaFileUri": media_uri},
            MediaFormat=media_format,
            OutputBucketName=CONTENT_BUCKET,
            OutputKey=output_key,
        )
        logger.info(f"Started Transcribe job {job_name}", extra={"content_id": content_id})
        return job_name
    except Exception as exc:
        logger.error(f"Failed to start Transcribe job: {exc}", exc_info=True)
        return None


# ============================================================
# Sync handler (small files)
# ============================================================

def lambda_handler(event: dict, context: Any) -> dict:
    """Process SQS messages for small files using sync Bedrock API."""
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            _process_embedding_sync(body)
        except Exception as exc:
            logger.error(f"Failed to process message {message_id}: {exc}", exc_info=True)
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def _process_embedding_sync(msg: dict) -> None:
    content_id = msg["content_id"]
    s3_key = msg["s3_key"]
    s3_bucket = msg.get("s3_bucket", CONTENT_BUCKET)
    modality = msg["modality"]
    task_id = msg["task_id"]
    user_id = msg["user_id"]
    created_at = msg["created_at"]

    with LogContext(logger, task_id=task_id, content_id=content_id, user_id=user_id):
        logger.info(f"Processing sync embedding for modality={modality}")
        start_ms = int(time.time() * 1000)

        try:
            update_task_status(task_id, user_id, created_at, "processing")

            s3_uri = build_s3_uri(s3_bucket, s3_key)
            embedding_vector = None
            mime_type = msg.get("mime_type")

            if modality == "text":
                text = msg.get("text_content") or read_text_object(s3_bucket, s3_key)
                embedding_vector = generate_embedding_sync(modality="text", s3_uri=s3_uri, text=text)

            elif modality == "image":
                file_bytes = read_object(s3_bucket, s3_key)
                mime_type = mime_type or "image/jpeg"
                embedding_vector = generate_embedding_sync(
                    modality="image",
                    s3_uri=s3_uri,
                    file_bytes=file_bytes,
                    mime_type=mime_type,
                )

            elif modality in ("audio", "video"):
                # Start Transcribe job in parallel with Bedrock embedding
                job_name = _start_transcribe_job(s3_key, s3_bucket, content_id, user_id, mime_type)
                if job_name:
                    update_content_transcribe_status(content_id, user_id, "pending",
                                                     transcribe_job_name=job_name)

                file_bytes = read_object(s3_bucket, s3_key)
                try:
                    embedding_vector = generate_embedding_sync(
                        modality=modality,
                        s3_uri=s3_uri,
                        file_bytes=file_bytes,
                        mime_type=mime_type,
                    )
                except ClientError as exc:
                    if modality == "audio" and "30 second" in str(exc):
                        # Audio exceeds sync 30s limit → redirect to async segmented embedding
                        output_prefix = build_embeddings_output_prefix(content_id)
                        output_s3_uri = build_s3_uri(EMBEDDINGS_OUTPUT_BUCKET, output_prefix)
                        invocation_arn = start_async_embed_audio_video(
                            s3_uri=s3_uri,
                            modality=modality,
                            output_s3_uri=output_s3_uri,
                            segment_duration_seconds=30,
                            mime_type=mime_type,
                        )
                        update_task_status(
                            task_id, user_id, created_at,
                            status="processing",
                            async_invocation_arn=invocation_arn,
                            segment_duration_seconds=30,
                        )
                        logger.info("Audio >30s redirected to async embedding", extra={"invocation_arn": invocation_arn})
                        return
                    raise

            elif modality == "document":
                mime_type = mime_type or "text/plain"
                if mime_type == "text/plain":
                    file_bytes = read_object(s3_bucket, s3_key)
                    text = file_bytes.decode("utf-8", errors="replace")
                    embedding_vector = generate_embedding_sync(modality="text", s3_uri=s3_uri, text=text)
                else:
                    # PDF/DOCX: Nova MME has no sync document modality; start async job and return.
                    # The embedding_poller will complete this task once the async job finishes.
                    output_prefix = build_embeddings_output_prefix(content_id)
                    output_s3_uri = build_s3_uri(EMBEDDINGS_OUTPUT_BUCKET, output_prefix)
                    invocation_arn = start_async_embed_document(s3_uri=s3_uri, output_s3_uri=output_s3_uri)
                    update_task_status(
                        task_id, user_id, created_at,
                        status="processing",
                        async_invocation_arn=invocation_arn,
                    )
                    logger.info("PDF/document redirected to async embedding", extra={"invocation_arn": invocation_arn})
                    return

            if embedding_vector is None:
                raise ValueError(f"No embedding generated for modality={modality}")

            # Store in S3 Vectors
            vector_key = content_id
            put_vectors([{
                "key": vector_key,
                "data": {"float32": embedding_vector},
                "metadata": {
                    "content_id": content_id,
                    "user_id": user_id,
                    "modality": modality,
                    "filename": msg.get("filename", ""),
                    "created_at": msg.get("created_at", now_iso()),
                },
            }])

            # Update DynamoDB
            processing_time = int(time.time() * 1000) - start_ms
            put_embedding_metadata(
                content_id=content_id,
                model_id=MODEL_ID,
                vector_dimension=len(embedding_vector),
                s3_vectors_key=vector_key,
            )
            mark_content_indexed(content_id, user_id)
            update_task_status(
                task_id, user_id, created_at,
                status="completed",
                processing_time_ms=processing_time,
                result_summary=f"Generated {len(embedding_vector)}-dim embedding via sync API",
            )
            logger.info(f"Sync embedding complete in {processing_time}ms")

        except Exception as exc:
            processing_time = int(time.time() * 1000) - start_ms
            logger.error(f"Embedding generation failed: {exc}", exc_info=True)
            update_task_status(
                task_id, user_id, created_at,
                status="failed",
                error_message=str(exc),
                processing_time_ms=processing_time,
            )
            raise  # Re-raise so SQS retries


# ============================================================
# Large file handler (starts async Bedrock jobs)
# ============================================================

def large_file_handler(event: dict, context: Any) -> dict:
    """Process SQS messages for large files by starting async Bedrock jobs."""
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            _start_async_embedding(body)
        except Exception as exc:
            logger.error(f"Failed to start async job for {message_id}: {exc}", exc_info=True)
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def _start_async_embedding(msg: dict) -> None:
    content_id = msg["content_id"]
    s3_key = msg["s3_key"]
    s3_bucket = msg.get("s3_bucket", CONTENT_BUCKET)
    modality = msg["modality"]
    task_id = msg["task_id"]
    user_id = msg["user_id"]
    created_at = msg["created_at"]

    with LogContext(logger, task_id=task_id, content_id=content_id, user_id=user_id):
        logger.info(f"Starting async Bedrock job for modality={modality}")

        update_task_status(task_id, user_id, created_at, "processing")

        s3_uri = build_s3_uri(s3_bucket, s3_key)
        output_prefix = build_embeddings_output_prefix(content_id)
        output_s3_uri = build_s3_uri(EMBEDDINGS_OUTPUT_BUCKET, output_prefix)
        mime_type = msg.get("mime_type")

        try:
            seg_dur = None
            if modality in ("audio", "video"):
                # Start Transcribe job in parallel with Bedrock async embedding
                job_name = _start_transcribe_job(s3_key, s3_bucket, content_id, user_id, mime_type)
                if job_name:
                    update_content_transcribe_status(content_id, user_id, "pending",
                                                     transcribe_job_name=job_name)

                seg_dur = 10
                invocation_arn = start_async_embed_audio_video(
                    s3_uri=s3_uri,
                    modality=modality,
                    output_s3_uri=output_s3_uri,
                    segment_duration_seconds=seg_dur,
                    mime_type=mime_type,
                )
            elif modality == "document":
                invocation_arn = start_async_embed_document(
                    s3_uri=s3_uri,
                    output_s3_uri=output_s3_uri,
                )
            else:
                raise ValueError(f"Unexpected modality for async processing: {modality}")

            # Store the invocation ARN so the poller can check status
            update_task_status(
                task_id, user_id, created_at,
                status="processing",
                async_invocation_arn=invocation_arn,
                segment_duration_seconds=seg_dur,
            )
            logger.info(f"Async job started", extra={"invocation_arn": invocation_arn})

        except Exception as exc:
            logger.error(f"Failed to start async embedding job: {exc}", exc_info=True)
            update_task_status(task_id, user_id, created_at, status="failed", error_message=str(exc))
            raise
