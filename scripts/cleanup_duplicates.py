#!/usr/bin/env python3
"""Clean up duplicate content records, S3 files, S3 Vectors, and Transcribe jobs."""
import argparse
import boto3
from botocore.exceptions import ClientError

DDB_TABLE = "multimodal-retrieval-dev-content-dev"
S3_BUCKET = "multimodal-retrieval-dev-content-dev-778346837945"
VECTOR_BUCKET = "multimodal-retrieval-dev-vectors-dev"
VECTOR_INDEX = "content-embeddings"
USER_ID = "a8b1d300-d0d1-7066-0eb6-e58366f15504"
REGION = "us-west-2"

DUPLICATES = [
    {"content_id": "ed09daaf-5b83-4586-b041-e4ac7866135d", "filename": "Agentic AI.pdf", "keep_id": "9c706517"},
    {"content_id": "c6f361a3-7091-41fa-b4ee-cfb9643a1255", "filename": "image.png", "keep_id": "9c6fa50c"},
    {"content_id": "fdbe0ee6-f19f-4c15-aa2a-e2ca32331b9f", "filename": "xqy02日耳曼.mp3", "keep_id": "f1efb3f9"},
]


def find_vector_keys(s3v, content_id):
    """Probe for all vectors belonging to a content_id."""
    keys_to_try = []
    for i in range(50):
        keys_to_try.append(f"{content_id}#seg{i}")
    for i in range(30):
        keys_to_try.append(f"{content_id}#transcript#seg{i}")
    
    resp = s3v.get_vectors(
        vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
        keys=keys_to_try, returnMetadata=False,
    )
    return [v["key"] for v in resp.get("vectors", [])]


def cleanup(execute):
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(DDB_TABLE)
    s3 = boto3.client("s3", region_name=REGION)
    s3v = boto3.client("s3vectors", region_name=REGION)
    transcribe = boto3.client("transcribe", region_name=REGION)

    mode = "🔴 EXECUTE" if execute else "🟡 DRY-RUN"
    print(f"=== {mode} ===\n")

    for dup in DUPLICATES:
        cid = dup["content_id"]
        print(f"--- {dup['filename']} (delete {cid[:8]}..., keep {dup['keep_id']}) ---")

        # 1. DDB content
        ck = {"PK": f"USER#{USER_ID}", "SK": f"CONTENT#{cid}"}
        print(f"  [DDB] Content record")
        if execute: table.delete_item(Key=ck); print("    ✓")

        # 2. DDB embedding
        ek = {"PK": f"CONTENT#{cid}", "SK": "EMBEDDING"}
        print(f"  [DDB] Embedding record")
        if execute: table.delete_item(Key=ek); print("    ✓")

        # 3. S3 files
        prefix = f"uploads/{USER_ID}/{cid}/"
        objs = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix).get("Contents", [])
        print(f"  [S3]  {len(objs)} file(s) under {prefix[:40]}...")
        for o in objs:
            print(f"    - {o['Key'].split('/')[-1]} ({o['Size']} bytes)")
            if execute: s3.delete_object(Bucket=S3_BUCKET, Key=o["Key"]); print("      ✓")

        # 4. S3 Vectors
        vkeys = find_vector_keys(s3v, cid)
        print(f"  [S3V] {len(vkeys)} vectors")
        if execute and vkeys:
            s3v.delete_vectors(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX, keys=vkeys)
            print(f"    ✓ Deleted {len(vkeys)}")

        # 5. Transcribe job
        job_name = f"mmr-{cid}"[:40]
        try:
            transcribe.get_transcription_job(TranscriptionJobName=job_name)
            print(f"  [Transcribe] Job found: {job_name}")
            if execute: transcribe.delete_transcription_job(TranscriptionJobName=job_name); print("    ✓")
        except Exception:
            print(f"  [Transcribe] No job")

        # 6. Transcribe output in S3
        t_objs = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"transcribe-output/{cid}").get("Contents", [])
        if t_objs:
            print(f"  [S3]  {len(t_objs)} transcribe output file(s)")
            for o in t_objs:
                if execute: s3.delete_object(Bucket=S3_BUCKET, Key=o["Key"]); print(f"    ✓ {o['Key'].split('/')[-1]}")

        print()

    print(f"=== Done {'(executed)' if execute else '(dry-run, use --execute to delete)'} ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    cleanup(p.parse_args().execute)
