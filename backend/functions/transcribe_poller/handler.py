"""Transcribe Poller Lambda: checks Amazon Transcribe job statuses and processes results.

Triggered every minute by EventBridge Scheduler.
Queries DynamoDB for content records with transcribe_status='pending',
checks the Transcribe job status, and for completed jobs:
  1. Downloads the transcript JSON from S3
  2. Segments the transcript into ~500-char chunks at sentence boundaries
  3. Generates a text embedding for each chunk using Bedrock Nova MME
  4. Stores the embeddings in S3 Vectors with key: content_id#transcript#segN
  5. Updates the DynamoDB content record with transcribe_status and full transcript text
"""
import json
import os
import time
from typing import Any, Optional

import boto3

from shared.logger import get_logger, LogContext
from shared.bedrock_client import embed_text_sync
from shared.s3_client import put_vectors, read_text_object, CONTENT_BUCKET
from shared.dynamodb import (
    get_pending_transcribe_content,
    update_content_transcribe_status,
    now_iso,
)

logger = get_logger(__name__)

TRANSCRIBE_REGION = os.environ.get("AWS_REGION", "us-west-2")
MAX_ITEMS_PER_INVOCATION = 50
CHUNK_SIZE_CHARS = 500
# Sentence boundary characters (Chinese and Latin)
SENTENCE_ENDINGS = frozenset("。！？!?.")


def lambda_handler(event: dict, context: Any) -> dict:
    """Poll all pending Transcribe jobs and process completed ones."""
    logger.info("Transcribe poller started")
    items = get_pending_transcribe_content(limit=MAX_ITEMS_PER_INVOCATION)
    logger.info(f"Found {len(items)} content items pending transcription")

    transcribe = boto3.client("transcribe", region_name=TRANSCRIBE_REGION)
    completed = 0
    failed = 0
    still_pending = 0

    for item in items:
        data = item.get("data", {})
        content_id = item.get("GSI1PK", "").replace("CONTENT#", "")
        user_id = item.get("PK", "").replace("USER#", "")
        job_name = data.get("transcribe_job_name", "")
        modality = data.get("modality", "audio")

        if not job_name or not content_id or not user_id:
            continue

        with LogContext(logger, content_id=content_id, user_id=user_id):
            try:
                response = transcribe.get_transcription_job(TranscriptionJobName=job_name)
                job = response["TranscriptionJob"]
                status = job["TranscriptionJobStatus"]

                if status in ("IN_PROGRESS", "QUEUED"):
                    still_pending += 1
                    logger.info(f"Transcribe job {job_name} still {status}")
                    continue

                elif status == "COMPLETED":
                    _process_completed_job(content_id, user_id, modality)
                    completed += 1

                elif status == "FAILED":
                    reason = job.get("FailureReason", "Unknown failure")
                    logger.error(f"Transcribe job {job_name} failed: {reason}")
                    update_content_transcribe_status(content_id, user_id, "failed")
                    failed += 1

            except Exception as exc:
                logger.error(f"Error checking Transcribe job {job_name}: {exc}", exc_info=True)
                # Don't mark as failed; let it retry next cycle

    logger.info(f"Transcribe poller done: completed={completed}, failed={failed}, "
                f"still_pending={still_pending}")
    return {"completed": completed, "failed": failed, "still_pending": still_pending}


def _process_completed_job(content_id: str, user_id: str, modality: str) -> None:
    """Read transcript from S3, chunk it, embed each chunk, store in S3 Vectors."""
    output_key = f"transcripts/{user_id}/{content_id}/transcript.json"

    # Read transcript JSON written by Transcribe
    try:
        raw = read_text_object(CONTENT_BUCKET, output_key)
        transcript_json = json.loads(raw)
    except Exception as exc:
        logger.error(f"Failed to read transcript from S3 key={output_key}: {exc}", exc_info=True)
        update_content_transcribe_status(content_id, user_id, "failed")
        return

    # Extract full transcript text and word-level items
    try:
        full_transcript = transcript_json["results"]["transcripts"][0]["transcript"]
        word_items = [
            it for it in transcript_json["results"].get("items", [])
            if it.get("type") == "pronunciation"
        ]
    except (KeyError, IndexError) as exc:
        logger.error(f"Unexpected transcript JSON structure: {exc}")
        update_content_transcribe_status(content_id, user_id, "failed")
        return

    if not full_transcript.strip():
        logger.warning(f"Empty transcript for content_id={content_id}, marking complete")
        update_content_transcribe_status(content_id, user_id, "completed",
                                         transcript=full_transcript)
        return

    # Chunk transcript into semantically coherent ~500-char pieces
    chunks = _chunk_transcript(full_transcript, word_items)
    logger.info(f"Transcript chunked into {len(chunks)} segments", extra={"content_id": content_id})

    # Generate text embeddings and build vector records
    vectors_to_store = []
    for i, chunk in enumerate(chunks):
        try:
            vector = embed_text_sync(chunk["text"])
        except Exception as exc:
            logger.error(f"Failed to embed transcript chunk {i}: {exc}", exc_info=True)
            continue

        vector_key = f"{content_id}#transcript#seg{i}"
        metadata: dict = {
            "content_id": content_id,
            "user_id": user_id,
            "modality": "transcript",
            "source_modality": modality,
            "segment_index": i,
            # Store a snippet so search results can show matching text (truncated for metadata size)
            "transcript_text": chunk["text"][:400],
        }
        if chunk.get("start_time") is not None:
            metadata["start_time"] = chunk["start_time"]
        if chunk.get("end_time") is not None:
            metadata["end_time"] = chunk["end_time"]

        vectors_to_store.append({
            "key": vector_key,
            "data": {"float32": vector},
            "metadata": metadata,
        })

    if not vectors_to_store:
        logger.error("No transcript vectors generated")
        update_content_transcribe_status(content_id, user_id, "failed")
        return

    try:
        put_vectors(vectors_to_store)
    except Exception as exc:
        logger.error(f"Failed to store transcript vectors: {exc}", exc_info=True)
        update_content_transcribe_status(content_id, user_id, "failed")
        return

    # Store full transcript (truncated to 10k chars for DDB item size limits)
    update_content_transcribe_status(
        content_id, user_id, "completed",
        transcript=full_transcript[:10_000],
    )
    logger.info(f"Transcript processing complete: {len(vectors_to_store)} vectors stored")


def _chunk_transcript(full_text: str, word_items: list[dict]) -> list[dict]:
    """Split transcript into ~CHUNK_SIZE_CHARS chunks at sentence boundaries.

    Uses word-level timestamps from Transcribe items to record start/end times.
    Falls back to simple character splitting if no word items are available.
    """
    if not word_items:
        return _chunk_by_characters(full_text)

    # Build list of (word, start_time, end_time) tuples from Transcribe pronunciation items
    words = []
    for item in word_items:
        content = item.get("alternatives", [{}])[0].get("content", "")
        start_t = _parse_time(item.get("start_time"))
        end_t = _parse_time(item.get("end_time"))
        if content:
            words.append({"text": content, "start": start_t, "end": end_t})

    if not words:
        return _chunk_by_characters(full_text)

    chunks = []
    current_words: list[dict] = []
    current_len = 0

    for word in words:
        word_text = word["text"]
        current_words.append(word)
        current_len += len(word_text) + 1  # +1 for space

        # Check if we've reached the chunk size and hit a sentence boundary
        if current_len >= CHUNK_SIZE_CHARS and word_text and word_text[-1] in SENTENCE_ENDINGS:
            chunk_text = " ".join(w["text"] for w in current_words)
            chunks.append({
                "text": chunk_text,
                "start_time": current_words[0]["start"],
                "end_time": current_words[-1]["end"],
            })
            current_words = []
            current_len = 0

    # Flush remaining words
    if current_words:
        chunk_text = " ".join(w["text"] for w in current_words)
        chunks.append({
            "text": chunk_text,
            "start_time": current_words[0]["start"],
            "end_time": current_words[-1]["end"],
        })

    return chunks if chunks else [{"text": full_text, "start_time": None, "end_time": None}]


def _chunk_by_characters(text: str) -> list[dict]:
    """Fallback: split text into CHUNK_SIZE_CHARS chunks at sentence boundaries."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE_CHARS, len(text))
        # Try to break at a sentence boundary
        if end < len(text):
            for i in range(end, max(start, end - 100), -1):
                if text[i - 1] in SENTENCE_ENDINGS:
                    end = i
                    break
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({"text": chunk_text, "start_time": None, "end_time": None})
        start = end

    return chunks if chunks else [{"text": text, "start_time": None, "end_time": None}]


def _parse_time(value: Optional[str]) -> Optional[float]:
    """Parse a time string like '1.234' to float seconds."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
