"""Search Lambda: convert query to embedding and search S3 Vectors."""
import base64
import json
import os
import time
import uuid
from typing import Any, Optional

from shared.logger import get_logger, LogContext
from shared.models import (
    api_response, error_response, ValidationError,
    detect_modality, validate_file_size, validate_text_length,
    SUPPORTED_MIME_TYPES,
)
from shared.bedrock_client import (
    embed_text_sync, embed_image_sync, embed_audio_video_sync,
)
from shared.s3_client import (
    query_vectors, get_cloudfront_private_key, generate_cloudfront_signed_url,
    VECTOR_BUCKET_NAME, VECTOR_INDEX_NAME,
)
from shared.dynamodb import get_content_by_id, put_task, now_iso, get_task_by_id

logger = get_logger(__name__)

_cloudfront_private_key: Optional[str] = None  # module-level cache

DEFAULT_TOP_K = 10
MAX_TOP_K = 100
MIN_TOP_K = 1


def lambda_handler(event: dict, context: Any) -> dict:
    request_id = event.get("requestContext", {}).get("requestId", "")
    user_id = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
             .get("sub", "")
    )

    with LogContext(logger, request_id=request_id, user_id=user_id):
        if not user_id:
            return error_response(401, "Unauthorized", "UNAUTHORIZED", request_id=request_id)

        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

        return _search(body, user_id, request_id)


def _search(body: dict, user_id: str, request_id: str) -> dict:
    query_text: Optional[str] = body.get("query_text")
    query_file_b64: Optional[str] = body.get("query_file")  # base64-encoded file bytes
    query_file_type: Optional[str] = body.get("query_file_type")
    query_s3_key: Optional[str] = body.get("query_s3_key")  # S3 key for large files
    top_k = int(body.get("top_k", DEFAULT_TOP_K))
    modality_filter: Optional[list] = body.get("modality_filter")

    # Validate top_k
    top_k = max(MIN_TOP_K, min(MAX_TOP_K, top_k))

    if not query_text and not query_file_b64 and not query_s3_key:
        return error_response(
            400,
            "Either query_text, query_file (base64), or query_s3_key must be provided",
            "MISSING_QUERY",
            request_id=request_id,
        )

    start_ms = int(time.time() * 1000)
    query_id = str(uuid.uuid4())

    # Generate query embedding
    try:
        query_vector = _generate_query_embedding(
            query_text=query_text,
            query_file_b64=query_file_b64,
            query_file_type=query_file_type,
            query_s3_key=query_s3_key,
            modality_filter=modality_filter,
        )
    except ValidationError as exc:
        return error_response(400, exc.message, exc.error_code, details=exc.details, request_id=request_id)
    except Exception as exc:
        logger.error(f"Failed to generate query embedding: {exc}", exc_info=True)
        return error_response(500, "Failed to generate query embedding", "EMBEDDING_FAILED", request_id=request_id)

    # Expand audio/video modality filters to also search transcript vectors
    # Transcript vectors (modality='transcript') enable text→text matching for audio/video content
    search_modalities = list(modality_filter) if modality_filter else []
    if "audio" in search_modalities and "transcript" not in search_modalities:
        search_modalities.append("transcript")
    if "video" in search_modalities and "transcript" not in search_modalities:
        search_modalities.append("transcript")

    # Build S3 Vectors filter expression for modality
    filter_expr = None
    if search_modalities:
        if len(search_modalities) == 1:
            filter_expr = {"modality": {"$eq": search_modalities[0]}}
        else:
            filter_expr = {"modality": {"$in": search_modalities}}

    # Execute vector search - fetch extra to have enough for grouping across content_ids
    search_top_k = min(top_k * 5, MAX_TOP_K)
    raw_results = query_vectors(
        query_vector=query_vector,
        top_k=search_top_k,
        filter_expr=filter_expr,
    )

    # Group segments by content_id, keep top 3 per content, then apply top_k at content level
    grouped = _group_results_by_content(raw_results, top_k)

    # Enrich results with content metadata and signed URLs
    results = []
    global _cloudfront_private_key
    if _cloudfront_private_key is None:
        _cloudfront_private_key = get_cloudfront_private_key()

    for group in grouped:
        content_id = group["content_id"]
        content_item = get_content_by_id(content_id)
        if not content_item:
            continue

        data = content_item.get("data", {})
        s3_key = data.get("s3_key", "")
        modality = data.get("modality", "")
        expires_in = 14400 if modality in ("video", "audio") else 3600

        preview_url = generate_cloudfront_signed_url(
            s3_key=s3_key,
            expires_in_seconds=expires_in,
            private_key_pem=_cloudfront_private_key,
        )

        best_score = round(1.0 - float(group["best_distance"]), 6)

        segments = []
        for hit in group["hits"]:
            distance = hit.get("distance", 0.0)
            similarity_score = round(1.0 - float(distance), 6)
            meta = hit.get("metadata", {})

            # Extract segment_index from key (e.g. content_id#seg5) or metadata
            key = hit.get("key", "")
            if "#seg" in key:
                try:
                    seg_idx = int(key.split("#seg")[1])
                except (ValueError, IndexError):
                    seg_idx = meta.get("segment_index", 0)
            else:
                seg_idx = meta.get("segment_index", 0)

            seg_dur = meta.get("segment_duration_seconds")
            if seg_dur is None and modality in ("audio", "video"):
                seg_dur = 30  # safe default

            # For transcript segments: use Transcribe word timestamps if available
            start_time = meta.get("start_time")
            if start_time is not None:
                time_offset = float(start_time)
            elif seg_dur is not None:
                time_offset = seg_idx * seg_dur
            else:
                time_offset = None

            seg_record: dict = {
                "segment_index": seg_idx,
                "similarity_score": similarity_score,
                "time_offset_seconds": time_offset,
                "duration_seconds": seg_dur,
                "is_transcript": meta.get("modality") == "transcript",
            }
            # Include transcript text snippet when matched from a transcript vector
            if meta.get("transcript_text"):
                seg_record["transcript_text"] = meta["transcript_text"]

            segments.append(seg_record)

        # Sort segments by score descending
        segments.sort(key=lambda s: s["similarity_score"], reverse=True)

        result: dict = {
            "content_id": content_id,
            "best_score": best_score,
            "modality": modality,
            "filename": data.get("filename"),
            "file_size": data.get("file_size"),
            "preview_url": preview_url,
            "created_at": data.get("created_at"),
            "metadata": data.get("metadata", {}),
            "segments": segments,
            "transcribe_status": data.get("transcribe_status"),
        }
        if data.get("transcript"):
            result["transcript"] = data["transcript"]
        results.append(result)

    # Sort by best_score descending
    results.sort(key=lambda r: r["best_score"], reverse=True)

    processing_time = int(time.time() * 1000) - start_ms
    logger.info(f"Search complete: {len(results)} results in {processing_time}ms")

    return api_response(200, {
        "query_id": query_id,
        "results": results,
        "total_count": len(results),
        "top_k": top_k,
        "processing_time_ms": processing_time,
    })


def _get_embedding_purpose(modality_filter: Optional[list]) -> str:
    """Choose embeddingPurpose based on the modality filter for better retrieval alignment.

    Audio/video searches also include transcript (text) vectors, so use GENERIC_RETRIEVAL
    to produce a query embedding that aligns with both audio/video and text spaces.
    """
    if not modality_filter or len(modality_filter) != 1:
        return "GENERIC_RETRIEVAL"
    m = modality_filter[0]
    if m in ("audio", "video"):
        # Search is expanded to include transcript vectors → use generic for cross-modal alignment
        return "GENERIC_RETRIEVAL"
    elif m in ("text", "document"):
        return "TEXT_RETRIEVAL"
    elif m == "image":
        return "IMAGE_RETRIEVAL"
    return "GENERIC_RETRIEVAL"


def _apply_text_query_prefix(text: str, modality_filter: Optional[list]) -> str:
    """Prepend instruction prefix to a text query for better embedding alignment."""
    if modality_filter and len(modality_filter) == 1:
        m = modality_filter[0]
        if m in ("text", "document"):
            return f"Instruction: Given a query, retrieve passages that are relevant to the query:\nQuery: {text}"
        if m in ("audio", "video"):
            # Include transcript matching hint since we search transcript vectors too
            return f"Instruction: Find audio or video content, or a transcript, that matches the following:\nQuery: {text}"
    return f"Instruction: Find an image, video, audio or document that matches the following description:\nQuery: {text}"


def _generate_query_embedding(
    query_text: Optional[str],
    query_file_b64: Optional[str],
    query_file_type: Optional[str],
    modality_filter: Optional[list] = None,
    query_s3_key: Optional[str] = None,
) -> list[float]:
    """Generate an embedding vector for the query input."""
    purpose = _get_embedding_purpose(modality_filter)

    if query_text:
        validate_text_length(query_text)
        prefixed_text = _apply_text_query_prefix(query_text, modality_filter)
        return embed_text_sync(prefixed_text, embedding_purpose=purpose)

    if query_s3_key:
        # Large file already uploaded to S3 — use S3 URI directly (avoids loading into memory for audio/video)
        from shared.s3_client import build_s3_uri, CONTENT_BUCKET
        import boto3
        mime_type = query_file_type or "application/octet-stream"
        modality = detect_modality(mime_type)
        s3_client = boto3.client("s3")
        try:
            if modality == "image":
                obj = s3_client.get_object(Bucket=CONTENT_BUCKET, Key=query_s3_key)
                file_bytes = obj["Body"].read()
                validate_file_size(modality, len(file_bytes))
                return embed_image_sync(file_bytes, mime_type, embedding_purpose=purpose)
            elif modality in ("audio", "video"):
                s3_uri = build_s3_uri(CONTENT_BUCKET, query_s3_key)
                return embed_audio_video_sync(s3_uri, modality, embedding_purpose=purpose)
            else:
                raise ValidationError(f"Unsupported modality for query: {modality}", "UNSUPPORTED_QUERY_MODALITY")
        finally:
            try:
                s3_client.delete_object(Bucket=CONTENT_BUCKET, Key=query_s3_key)
            except Exception:
                pass

    if query_file_b64:
        try:
            file_bytes = base64.b64decode(query_file_b64)
        except Exception:
            raise ValidationError("query_file must be valid base64", "INVALID_BASE64")

        mime_type = query_file_type or "application/octet-stream"
        modality = detect_modality(mime_type)
        validate_file_size(modality, len(file_bytes))

        if modality == "image":
            return embed_image_sync(file_bytes, mime_type, embedding_purpose=purpose)
        elif modality in ("audio", "video"):
            from shared.s3_client import upload_bytes, build_s3_uri, CONTENT_BUCKET
            import uuid
            temp_key = f"tmp/query/{uuid.uuid4()}"
            upload_bytes(temp_key, file_bytes, mime_type)
            s3_uri = build_s3_uri(CONTENT_BUCKET, temp_key)
            result = embed_audio_video_sync(s3_uri, modality, embedding_purpose=purpose)
            try:
                import boto3
                boto3.client("s3").delete_object(Bucket=CONTENT_BUCKET, Key=temp_key)
            except Exception:
                pass
            return result
        else:
            raise ValidationError(f"Unsupported modality for query: {modality}", "UNSUPPORTED_QUERY_MODALITY")

    raise ValidationError("No query provided", "MISSING_QUERY")


def _group_results_by_content(raw_results: list[dict], top_k: int) -> list[dict]:
    """Group hits by content_id, keeping up to 3 top-scoring segments per content.

    Returns a list of groups sorted by best_distance ascending (most similar first),
    limited to top_k unique content_ids.
    """
    groups: dict[str, list[dict]] = {}
    for hit in raw_results:
        key = hit.get("key", "")
        content_id = key.split("#")[0]
        if content_id not in groups:
            groups[content_id] = []
        groups[content_id].append(hit)

    result_groups = []
    for content_id, hits in groups.items():
        # Sort by distance ascending (most similar first), keep top 3
        hits.sort(key=lambda h: h.get("distance", 1.0))
        top_hits = hits[:3]
        result_groups.append({
            "content_id": content_id,
            "hits": top_hits,
            "best_distance": top_hits[0].get("distance", 1.0),
        })

    # Sort groups by best_distance ascending, apply top_k at content level
    result_groups.sort(key=lambda g: g["best_distance"])
    return result_groups[:top_k]
