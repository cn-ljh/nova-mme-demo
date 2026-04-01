"""Embedding Poller Lambda: checks async Bedrock job statuses and processes results.

Triggered every minute by EventBridge Scheduler.
Queries DynamoDB for tasks with status=processing and async_invocation_arn set,
then calls Bedrock GetAsyncInvoke to check completion.
"""
import json
import os
import time
from typing import Any

import boto3

from shared.logger import get_logger, LogContext
from shared.bedrock_client import get_async_job_status
from shared.s3_client import put_vectors, read_text_object, EMBEDDINGS_OUTPUT_BUCKET
from shared.dynamodb import (
    get_pending_async_tasks, update_task_status,
    mark_content_indexed, put_embedding_metadata, now_iso,
)

logger = get_logger(__name__)

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-2-multimodal-embeddings-v1:0")
MAX_TASKS_PER_INVOCATION = 50


def lambda_handler(event: dict, context: Any) -> dict:
    """Poll all pending async embedding jobs and process completed ones."""
    logger.info("Embedding poller started")
    tasks = get_pending_async_tasks(limit=MAX_TASKS_PER_INVOCATION)
    logger.info(f"Found {len(tasks)} pending async tasks")

    processed = 0
    failed = 0
    still_pending = 0

    for task in tasks:
        data = task.get("data", {})
        task_id_full = task.get("GSI1PK", "").replace("TASK#", "")
        # Extract task info from GSI1PK or SK
        task_id = data.get("task_id") or task_id_full
        user_id = task.get("PK", "").replace("USER#", "")
        sk = task.get("SK", "")
        # SK format: TASK#{created_at}#{task_id}
        parts = sk.split("#", 2)
        created_at = parts[1] if len(parts) > 1 else ""
        content_id = data.get("content_id", "")
        invocation_arn = data.get("async_invocation_arn", "")
        modality = data.get("modality", "")
        segment_duration_seconds = data.get("segment_duration_seconds")
        if segment_duration_seconds is not None:
            segment_duration_seconds = int(segment_duration_seconds)

        if not invocation_arn:
            continue

        with LogContext(logger, task_id=task_id, content_id=content_id, user_id=user_id):
            try:
                job_info = get_async_job_status(invocation_arn)
                status = job_info["status"]

                if status == "InProgress":
                    still_pending += 1
                    logger.info("Async job still in progress")
                    continue

                elif status == "Completed":
                    _process_async_result(
                        content_id=content_id,
                        user_id=user_id,
                        task_id=task_id,
                        created_at=created_at,
                        modality=modality,
                        output_s3_uri=job_info.get("output_s3_uri", ""),
                        invocation_arn=invocation_arn,
                        segment_duration_seconds=segment_duration_seconds,
                    )
                    processed += 1

                elif status == "Failed":
                    failure_msg = job_info.get("failure_message", "Bedrock async job failed")
                    logger.error(f"Async Bedrock job failed: {failure_msg}")
                    update_task_status(task_id, user_id, created_at, status="failed", error_message=failure_msg)
                    failed += 1

            except Exception as exc:
                logger.error(f"Error processing async task: {exc}", exc_info=True)
                # Don't mark as failed yet; let it retry on next poll cycle

    logger.info(f"Poller done: processed={processed}, failed={failed}, still_pending={still_pending}")
    return {"processed": processed, "failed": failed, "still_pending": still_pending}


def _process_async_result(
    content_id: str,
    user_id: str,
    task_id: str,
    created_at: str,
    modality: str,
    output_s3_uri: str,
    invocation_arn: str = "",
    segment_duration_seconds: int = None,
) -> None:
    """Read async Bedrock output from S3, store vectors, update DynamoDB.

    GetAsyncInvoke returns output_s3_uri = s3://bucket/{content_id}/{invocation_id}
    (Bedrock appends the invocation ID to the URI we specified).
    Files written: {output_uri}/segmented-embedding-result.json and .jsonl embedding files.
    Each JSONL line: {"embedding": [...], "status": "SUCCESS", "segmentMetadata": {...}}
    """
    start_ms = int(time.time() * 1000)

    # output_s3_uri from GetAsyncInvoke already includes the invocation ID subpath
    uri_parts = output_s3_uri.replace("s3://", "").split("/", 1)
    output_bucket = uri_parts[0]
    output_prefix = uri_parts[1].rstrip("/") if len(uri_parts) > 1 else ""

    # Read the result manifest to discover output JSONL files
    result_key = f"{output_prefix}/segmented-embedding-result.json"
    try:
        result_json = json.loads(read_text_object(output_bucket, result_key))
    except Exception as exc:
        logger.error(f"Failed to read result manifest: {exc}")
        update_task_status(task_id, user_id, created_at, status="failed",
                           error_message=f"Failed to read embedding output: {exc}")
        return

    # Collect all output JSONL files from the manifest
    jsonl_uris = [
        r["outputFileUri"]
        for r in result_json.get("embeddingResults", [])
        if r.get("status") == "SUCCESS" and r.get("outputFileUri", "").endswith(".jsonl")
    ]
    if not jsonl_uris:
        logger.error("No successful embedding output files in manifest")
        update_task_status(task_id, user_id, created_at, status="failed",
                           error_message="Empty embedding output from Bedrock")
        return

    # Parse all JSONL files: each line is {"embedding": [...], "segmentMetadata": {...}}
    all_embeddings: list[tuple[int, list]] = []  # (segment_index, vector)
    for uri in jsonl_uris:
        try:
            parts = uri.replace("s3://", "").split("/", 1)
            bucket, key = parts[0], parts[1]
            raw_jsonl = read_text_object(bucket, key)
            for line in raw_jsonl.splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if item.get("status") != "SUCCESS":
                    continue
                vector = item.get("embedding", [])
                if not vector:
                    continue
                seg_idx = item.get("segmentMetadata", {}).get("segmentIndex", len(all_embeddings))
                all_embeddings.append((seg_idx, vector))
        except Exception as exc:
            logger.error(f"Failed to read JSONL output {uri}: {exc}")

    # Fall back to 30s for audio/video if not stored in task data
    if segment_duration_seconds is None and modality in ("audio", "video"):
        segment_duration_seconds = 30

    vectors_to_store = []
    for seg_idx, vector in sorted(all_embeddings, key=lambda x: x[0]):
        key = f"{content_id}#seg{seg_idx}" if len(all_embeddings) > 1 else content_id
        meta = {
            "content_id": content_id,
            "user_id": user_id,
            "modality": modality,
            "segment_index": seg_idx,
        }
        if segment_duration_seconds is not None:
            meta["segment_duration_seconds"] = segment_duration_seconds
        vectors_to_store.append({
            "key": key,
            "data": {"float32": vector},
            "metadata": meta,
        })

    if not vectors_to_store:
        logger.error("No vectors found in async output")
        update_task_status(task_id, user_id, created_at, status="failed",
                           error_message="Empty embedding output from Bedrock")
        return

    try:
        put_vectors(vectors_to_store)
    except Exception as exc:
        logger.error(f"Failed to store vectors in S3 Vectors: {exc}", exc_info=True)
        update_task_status(task_id, user_id, created_at, status="failed",
                           error_message=f"Failed to store vectors: {exc}")
        return

    processing_time = int(time.time() * 1000) - start_ms
    vector_count = len(vectors_to_store)
    vector_dim = len(vectors_to_store[0]["data"]["float32"])

    put_embedding_metadata(
        content_id=content_id,
        model_id=MODEL_ID,
        vector_dimension=vector_dim,
        s3_vectors_key=vectors_to_store[0]["key"],
    )
    mark_content_indexed(content_id, user_id)
    update_task_status(
        task_id, user_id, created_at,
        status="completed",
        processing_time_ms=processing_time,
        result_summary=f"Generated {vector_count} segment(s), {vector_dim}-dim via async API",
    )
    logger.info(f"Async embedding processed: {vector_count} vectors in {processing_time}ms")
