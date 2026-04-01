#!/usr/bin/env python3
"""Clean up duplicate content records, S3 files, S3 Vectors, and Transcribe jobs.

Usage:
  python3 scripts/cleanup_duplicates.py                # dry-run
  python3 scripts/cleanup_duplicates.py --execute      # actually delete

Requires env vars or samconfig for:
  DDB_TABLE, S3_BUCKET, VECTOR_BUCKET, VECTOR_INDEX
Or reads from CloudFormation stack outputs automatically.
"""
import argparse
import os
import boto3
from botocore.exceptions import ClientError


def get_stack_config(stack_name="multimodal-retrieval-dev", region="us-west-2"):
    """Read resource names from CloudFormation stack outputs."""
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
        outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
        return {
            "DDB_TABLE": outputs.get("ContentTableName", ""),
            "S3_BUCKET": outputs.get("ContentBucketName", ""),
            "VECTOR_BUCKET": outputs.get("VectorBucketName", ""),
            "VECTOR_INDEX": "content-embeddings",
        }
    except Exception as e:
        print(f"Warning: could not read stack outputs: {e}")
        return {}


def find_vector_keys(s3v, vector_bucket, vector_index, content_id):
    """Probe for all vectors belonging to a content_id."""
    keys_to_try = [f"{content_id}#seg{i}" for i in range(50)]
    keys_to_try += [f"{content_id}#transcript#seg{i}" for i in range(30)]
    resp = s3v.get_vectors(
        vectorBucketName=vector_bucket, indexName=vector_index,
        keys=keys_to_try, returnMetadata=False,
    )
    return [v["key"] for v in resp.get("vectors", [])]


def scan_duplicates(table):
    """Find duplicate content (same filename+file_size) in the table."""
    from collections import defaultdict
    resp = table.scan()
    by_key = defaultdict(list)
    for item in resp["Items"]:
        if item.get("entity_type") != "CONTENT":
            continue
        data = item.get("data", {})
        key = (data.get("filename"), int(data.get("file_size", 0)))
        user_id = item["PK"].replace("USER#", "")
        content_id = item["SK"].replace("CONTENT#", "")
        by_key[key].append({"content_id": content_id, "user_id": user_id, "item": item})
    return {k: v for k, v in by_key.items() if len(v) > 1}


def cleanup(execute, region="us-west-2"):
    config = get_stack_config(region=region)
    ddb_table = os.environ.get("DDB_TABLE", config.get("DDB_TABLE", ""))
    s3_bucket = os.environ.get("S3_BUCKET", config.get("S3_BUCKET", ""))
    vector_bucket = os.environ.get("VECTOR_BUCKET", config.get("VECTOR_BUCKET", ""))
    vector_index = os.environ.get("VECTOR_INDEX", config.get("VECTOR_INDEX", "content-embeddings"))

    if not all([ddb_table, s3_bucket, vector_bucket]):
        print("Error: could not determine resource names. Set DDB_TABLE, S3_BUCKET, VECTOR_BUCKET env vars.")
        return

    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(ddb_table)
    s3 = boto3.client("s3", region_name=region)
    s3v = boto3.client("s3vectors", region_name=region)
    transcribe = boto3.client("transcribe", region_name=region)

    mode = "🔴 EXECUTE" if execute else "🟡 DRY-RUN"
    print(f"=== {mode} ===")
    print(f"Table: {ddb_table} | Bucket: {s3_bucket} | Vectors: {vector_bucket}/{vector_index}\n")

    duplicates = scan_duplicates(table)
    if not duplicates:
        print("✅ No duplicates found!")
        return

    print(f"Found {len(duplicates)} duplicate group(s):\n")

    for (filename, file_size), entries in duplicates.items():
        print(f"--- {filename} ({file_size} bytes, {len(entries)} copies) ---")
        # Keep first, delete rest
        keep = entries[0]
        to_delete = entries[1:]
        print(f"  KEEP: {keep['content_id'][:12]}...")

        for dup in to_delete:
            cid = dup["content_id"]
            uid = dup["user_id"]
            print(f"  DELETE: {cid[:12]}...")

            # DDB
            for key in [
                {"PK": f"USER#{uid}", "SK": f"CONTENT#{cid}"},
                {"PK": f"CONTENT#{cid}", "SK": "EMBEDDING"},
            ]:
                print(f"    [DDB] {key['SK']}")
                if execute:
                    table.delete_item(Key=key)
                    print(f"      ✓")

            # S3
            prefix = f"uploads/{uid}/{cid}/"
            objs = s3.list_objects_v2(Bucket=s3_bucket, Prefix=prefix).get("Contents", [])
            print(f"    [S3]  {len(objs)} file(s)")
            if execute:
                for o in objs:
                    s3.delete_object(Bucket=s3_bucket, Key=o["Key"])
                print(f"      ✓")

            # Vectors
            vkeys = find_vector_keys(s3v, vector_bucket, vector_index, cid)
            print(f"    [S3V] {len(vkeys)} vectors")
            if execute and vkeys:
                s3v.delete_vectors(vectorBucketName=vector_bucket, indexName=vector_index, keys=vkeys)
                print(f"      ✓")

            # Transcribe
            job_name = f"mmr-{cid}"[:40]
            try:
                transcribe.get_transcription_job(TranscriptionJobName=job_name)
                print(f"    [Transcribe] Job: {job_name}")
                if execute:
                    transcribe.delete_transcription_job(TranscriptionJobName=job_name)
                    print(f"      ✓")
            except Exception:
                pass

        print()

    print(f"=== Done {'(executed)' if execute else '(dry-run, use --execute to delete)'} ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Find and clean duplicate content")
    p.add_argument("--execute", action="store_true", help="Actually delete (default: dry-run)")
    p.add_argument("--region", default="us-west-2")
    cleanup(p.parse_args().execute, p.parse_args().region)
