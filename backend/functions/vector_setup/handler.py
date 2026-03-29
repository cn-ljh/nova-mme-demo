"""CloudFormation Custom Resource Lambda: creates S3 Vectors bucket and index."""
import json
import os
import urllib.request
from typing import Any


def _send_cfn_response(response_url: str, event: dict, physical_id: str, status: str, reason: str) -> None:
    """Send response to CloudFormation - must work even if imports fail."""
    response_body = json.dumps({
        "Status": status,
        "Reason": reason or "See CloudWatch logs for details",
        "PhysicalResourceId": physical_id,
        "StackId": event.get("StackId", ""),
        "RequestId": event.get("RequestId", ""),
        "LogicalResourceId": event.get("LogicalResourceId", ""),
    }).encode()

    req = urllib.request.Request(
        response_url,
        data=response_body,
        headers={"Content-Type": "", "Content-Length": str(len(response_body))},
        method="PUT",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as exc:
        print(f"Failed to send CloudFormation response: {exc}")


def lambda_handler(event: dict, context: Any) -> None:
    response_url = event.get("ResponseURL", "")
    request_type = event.get("RequestType", "")
    props = event.get("ResourceProperties", {})

    vector_bucket_name = props.get("VectorBucketName", "")
    physical_id = f"vector-bucket-{vector_bucket_name}"
    status = "SUCCESS"
    reason = ""

    try:
        if request_type in ("Create", "Update"):
            # Import here so Delete still works even if layer is missing
            from shared.logger import get_logger
            from shared.s3_client import create_vector_bucket_and_index

            logger = get_logger(__name__)
            index_name = props.get("IndexName", "content-embeddings")
            dimension = int(props.get("Dimension", 1024))
            distance_metric = props.get("DistanceMetric", "cosine")

            logger.info(f"Creating/updating vector bucket: {vector_bucket_name}")
            create_vector_bucket_and_index(
                vector_bucket_name=vector_bucket_name,
                index_name=index_name,
                dimension=dimension,
                distance_metric=distance_metric,
            )
            logger.info("Vector bucket and index created successfully")

        elif request_type == "Delete":
            # Don't delete the vector bucket on stack deletion to preserve data
            print(f"Delete requested for vector bucket {vector_bucket_name} - skipping to preserve data")

    except Exception as exc:
        print(f"Vector setup error: {exc}")
        if request_type == "Delete":
            # Always succeed on delete to prevent stack deletion from hanging
            status = "SUCCESS"
            reason = f"Delete skipped (error ignored): {exc}"
        else:
            status = "FAILED"
            reason = str(exc)

    if response_url:
        _send_cfn_response(response_url, event, physical_id, status, reason)
