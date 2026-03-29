"""S3 and S3 Vectors operations."""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from shared.logger import get_logger

logger = get_logger(__name__)

CONTENT_BUCKET = os.environ.get("CONTENT_BUCKET", "")
EMBEDDINGS_OUTPUT_BUCKET = os.environ.get("EMBEDDINGS_OUTPUT_BUCKET", "")
VECTOR_BUCKET_NAME = os.environ.get("VECTOR_BUCKET_NAME", "")
VECTOR_INDEX_NAME = os.environ.get("VECTOR_INDEX_NAME", "content-embeddings")
CLOUDFRONT_DOMAIN = os.environ.get("CLOUDFRONT_DOMAIN", "")


# ============================================================
# Regular S3 operations
# ============================================================

def generate_presigned_upload_url(
    s3_key: str,
    mime_type: str,
    max_size_bytes: int,
    expires_in: int = 3600,
) -> dict:
    """Generate a presigned POST URL for direct-to-S3 upload by the client."""
    from botocore.config import Config
    s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    conditions = [
        {"Content-Type": mime_type},
        ["content-length-range", 1, max_size_bytes],
    ]
    response = s3.generate_presigned_post(
        Bucket=CONTENT_BUCKET,
        Key=s3_key,
        Fields={"Content-Type": mime_type},
        Conditions=conditions,
        ExpiresIn=expires_in,
    )
    return response  # {"url": ..., "fields": {...}}


def get_presigned_download_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned GET URL for downloading a file directly from S3."""
    from botocore.config import Config
    s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": CONTENT_BUCKET, "Key": s3_key},
        ExpiresIn=expires_in,
    )


def upload_bytes(s3_key: str, data: bytes, content_type: str) -> None:
    """Upload raw bytes to the content bucket (for small files via API Gateway)."""
    s3 = boto3.client("s3")
    s3.put_object(Bucket=CONTENT_BUCKET, Key=s3_key, Body=data, ContentType=content_type)


def read_object(bucket: str, key: str) -> bytes:
    """Read an S3 object and return its bytes."""
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def read_text_object(bucket: str, key: str) -> str:
    """Read an S3 object and return its content as a UTF-8 string."""
    return read_object(bucket, key).decode("utf-8")


def object_exists(bucket: str, key: str) -> bool:
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def build_s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def build_content_s3_key(user_id: str, content_id: str, filename: str) -> str:
    return f"uploads/{user_id}/{content_id}/{filename}"


def build_embeddings_output_prefix(content_id: str) -> str:
    return f"{content_id}/"


# ============================================================
# CloudFront signed URL generation
# ============================================================

def generate_cloudfront_signed_url(
    s3_key: str,
    expires_in_seconds: int = 3600,
    cloudfront_key_pair_id: Optional[str] = None,
    private_key_pem: Optional[str] = None,
) -> str:
    """Generate a CloudFront signed URL for content preview/download.

    Falls back to S3 presigned URL if CloudFront key is not configured.
    """
    cf_key_pair_id = cloudfront_key_pair_id or os.environ.get("CLOUDFRONT_KEY_PAIR_ID", "")
    domain = CLOUDFRONT_DOMAIN

    if not cf_key_pair_id or not private_key_pem or not domain:
        # Fallback: use S3 presigned URL
        logger.warning("CloudFront key not configured, falling back to S3 presigned URL")
        return get_presigned_download_url(s3_key, expires_in=expires_in_seconds)

    # Use CloudFront signed URL with canned policy
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    import struct, base64, hashlib
    from urllib.parse import quote

    expires = int(
        (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).timestamp()
    )
    url = f"https://{domain}/content/{s3_key}"
    message = (
        f'{{"Statement":[{{"Resource":"{url}",'
        f'"Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'
    )

    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA1())
    b64_sig = base64.b64encode(signature).decode().replace("+", "-").replace("=", "_").replace("/", "~")

    return (
        f"{url}?Expires={expires}"
        f"&Signature={b64_sig}"
        f"&Key-Pair-Id={cf_key_pair_id}"
    )


def get_cloudfront_private_key() -> str:
    """Fetch the CloudFront private key from Secrets Manager (cached in-process)."""
    import json
    secret_arn = os.environ.get("CLOUDFRONT_PRIVATE_KEY_SECRET_ARN", "")
    if not secret_arn:
        return ""
    try:
        sm = boto3.client("secretsmanager")
        response = sm.get_secret_value(SecretId=secret_arn)
        secret = json.loads(response["SecretString"])
        return secret.get("private_key", "")
    except Exception as e:
        logger.warning(f"Failed to fetch CloudFront private key, falling back to S3 presigned URL: {e}")
        return ""


# ============================================================
# S3 Vectors operations
# NOTE: S3 Vectors uses the 's3vectors' boto3 service client.
# API reference: https://docs.aws.amazon.com/s3/latest/userguide/s3-vectors.html
# ============================================================

def _s3vectors_client():
    return boto3.client("s3vectors", region_name=os.environ.get("AWS_REGION", "us-east-1"))


_vector_bucket_initialized = False


def _ensure_vector_bucket() -> None:
    """Lazy-init: create S3 Vectors bucket and index on first use."""
    global _vector_bucket_initialized
    if _vector_bucket_initialized:
        return
    try:
        create_vector_bucket_and_index(
            vector_bucket_name=VECTOR_BUCKET_NAME,
            index_name=VECTOR_INDEX_NAME,
            dimension=1024,
            distance_metric="cosine",
        )
    except Exception as e:
        logger.warning(f"Vector bucket init check: {e}")
    _vector_bucket_initialized = True


def put_vectors(vectors: list[dict]) -> None:
    """Store vectors in S3 Vectors index.

    Each vector dict: {
        "key": str,                 # unique key (e.g., content_id or content_id#seg0)
        "data": {"float32": [...]}, # 1024-dim float list
        "metadata": {...}           # searchable metadata
    }

    PutVectors API has a max batch size of 500. Vectors are automatically chunked.
    """
    if not vectors:
        return
    _ensure_vector_bucket()
    client = _s3vectors_client()
    batch_size = 500
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        client.put_vectors(
            vectorBucketName=VECTOR_BUCKET_NAME,
            indexName=VECTOR_INDEX_NAME,
            vectors=batch,
        )
        logger.info(f"Stored batch of {len(batch)} vectors (offset {i}) in S3 Vectors")
    logger.info(f"Stored {len(vectors)} vectors total in S3 Vectors")


def query_vectors(
    query_vector: list[float],
    top_k: int = 10,
    filter_expr: Optional[dict] = None,
) -> list[dict]:
    """Query S3 Vectors for similar vectors.
    Returns list of {key, distance, metadata}.
    """
    client = _s3vectors_client()
    kwargs: dict = {
        "vectorBucketName": VECTOR_BUCKET_NAME,
        "indexName": VECTOR_INDEX_NAME,
        "queryVector": {"float32": query_vector},
        "topK": top_k,
        "returnDistance": True,
        "returnMetadata": True,
    }
    if filter_expr:
        kwargs["filter"] = filter_expr

    response = client.query_vectors(**kwargs)
    return response.get("vectors", [])


def get_vector(key: str) -> Optional[dict]:
    """Retrieve a specific vector by key."""
    client = _s3vectors_client()
    try:
        response = client.get_vectors(
            vectorBucketName=VECTOR_BUCKET_NAME,
            indexName=VECTOR_INDEX_NAME,
            keys=[key],
        )
        vectors = response.get("vectors", [])
        return vectors[0] if vectors else None
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NoSuchKey"):
            return None
        raise


def delete_vectors(keys: list[str]) -> None:
    """Delete vectors by key."""
    if not keys:
        return
    client = _s3vectors_client()
    client.delete_vectors(
        vectorBucketName=VECTOR_BUCKET_NAME,
        indexName=VECTOR_INDEX_NAME,
        keys=keys,
    )


def create_vector_bucket_and_index(
    vector_bucket_name: str,
    index_name: str,
    dimension: int = 1024,
    distance_metric: str = "cosine",
) -> None:
    """Create the S3 Vectors bucket and index. Called by the custom resource Lambda."""
    client = _s3vectors_client()

    # Create bucket (idempotent)
    try:
        client.create_vector_bucket(vectorBucketName=vector_bucket_name)
        logger.info(f"Created vector bucket: {vector_bucket_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
            logger.info(f"Vector bucket already exists: {vector_bucket_name}")
        else:
            raise

    # Create index (idempotent)
    # Note: metadataConfiguration.nonFilterableMetadataKeys lists keys NOT available for filtering.
    # Keys not listed (user_id, modality, content_id, segment_index) are filterable by default.
    try:
        client.create_index(
            vectorBucketName=vector_bucket_name,
            indexName=index_name,
            dataType="float32",
            dimension=dimension,
            distanceMetric=distance_metric,
            metadataConfiguration={
                "nonFilterableMetadataKeys": ["segment_index"],
            },
        )
        logger.info(f"Created vector index: {index_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("IndexAlreadyExists", "ConflictException"):
            logger.info(f"Vector index already exists: {index_name}")
        else:
            raise
