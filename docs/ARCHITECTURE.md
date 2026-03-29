# Architecture: Multimodal Content Retrieval

## Overview

A serverless multimodal content retrieval system built on AWS. Users upload text, images, audio, video, and documents; the system generates unified vector embeddings using Amazon Bedrock Nova MME and stores them in S3 Vectors for cross-modal semantic search.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Users (Browser)                          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   Amazon CloudFront    │
                    │  (CDN + unified entry) │
                    └───┬───────────────┬───┘
                        │               │
              ┌─────────▼──┐    ┌───────▼────────┐
              │  S3 Bucket  │    │  API Gateway   │
              │ (React SPA) │    │ (REST + Cognito│
              └─────────────┘    │  Authorizer)   │
                                 └──────┬─────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                    Lambda Functions                 │
              │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
              │  │   Auth   │  │ Content  │  │     Search       │ │
              │  │  Lambda  │  │  Lambda  │  │     Lambda       │ │
              │  └────┬─────┘  └────┬─────┘  └────────┬─────────┘ │
              │       │             │                   │           │
              └───────┼─────────────┼───────────────────┼───────────┘
                      │             │                   │
              ┌───────▼──┐   ┌──────▼───┐      ┌───────▼────────┐
              │ Cognito  │   │    SQS   │      │  S3 Vectors    │
              │ User Pool│   │ 2 Queues │      │ (1024-dim      │
              └──────────┘   └──────┬───┘      │  cosine)       │
                                    │           └────────────────┘
              ┌─────────────────────┼───────────────────────────────┐
              │              Async Embedding Pipeline                │
              │  ┌───────────────┐  │  ┌──────────────────────────┐ │
              │  │  Embedding    ◄──┘  │   EmbeddingPoller        │ │
              │  │  Lambda       │     │   Lambda                 │ │
              │  │ (sync embed)  │     │ (polls async Bedrock jobs│ │
              │  └──────┬────────┘     │  via EventBridge 1-min)  │ │
              │         │              └──────────┬───────────────┘ │
              └─────────┼─────────────────────────┼─────────────────┘
                        │                         │
              ┌─────────▼─────────────────────────▼─────────────────┐
              │                 Bedrock Nova MME                      │
              │   (amazon.nova-2-multimodal-embeddings-v1:0)         │
              │              region: us-east-1                        │
              └──────────────────────────────────────────────────────┘
                        │
              ┌─────────▼──────────┐   ┌─────────────────────────┐
              │     DynamoDB       │   │   S3 Content Bucket      │
              │  (single-table     │   │  (original uploads +     │
              │   tasks + content) │   │   async embedding output)│
              └────────────────────┘   └─────────────────────────┘
```

## AWS Services

| Service | Role |
|---------|------|
| **CloudFront** | CDN, unified entry point for frontend + API, signed URL generation |
| **S3 (frontend)** | Hosts React SPA static assets |
| **API Gateway** | REST API, Cognito Authorizer, Lambda proxy integration |
| **Cognito User Pool** | User registration, SRP authentication, JWT token issuance |
| **Lambda (×8)** | Serverless compute for all business logic |
| **SQS (×2)** | Decoupled embedding task queues with DLQ |
| **EventBridge Scheduler** | 1-minute cron to poll async Bedrock jobs and Transcribe jobs |
| **Bedrock Nova MME** | Multimodal embedding model (us-east-1 only) |
| **Amazon Transcribe** | Speech-to-text for audio/video content |
| **S3 Vectors** | Vector storage and cosine similarity search (1024-dim) |
| **S3 (content)** | Original file storage + async embedding output |
| **DynamoDB** | Single-table design: task and content metadata |
| **Secrets Manager** | CloudFront private key for signed URLs |
| **CloudWatch** | Structured JSON logs, X-Ray tracing |

## Lambda Functions

### auth
- **Trigger**: API Gateway `POST /api/auth/register`, `GET /api/auth/me`
- **Responsibility**: Wraps Cognito `AdminCreateUser` for registration; returns user profile from Cognito claims
- **Note**: Login uses Amplify SRP directly against Cognito (no Lambda proxy)

### content
- **Trigger**: API Gateway `POST /api/content/request-upload`, `POST /api/content/confirm-upload`, `POST /api/content/upload-text`, `GET /api/content/{id}`, `DELETE /api/content/{id}`
- **Responsibility**: Generates S3 presigned upload URLs (SigV4), creates CONTENT + TASK records in DynamoDB, enqueues embedding messages to SQS
- **Small vs large file routing**: Files ≤ 100MB or ≤ 30s → EmbeddingQueue (sync); large audio/video → LargeFileEmbeddingQueue (async)

### embedding
- **Trigger**: SQS `EmbeddingQueue` (batch size 1, visibility timeout 900s)
- **Responsibility**: Downloads file from S3, calls Bedrock Nova MME sync API (`InvokeModel`) with modality-specific `embeddingPurpose`, stores 1024-dim vector in S3 Vectors, updates task status in DynamoDB. For audio/video files, also starts an Amazon Transcribe job to generate a transcript for text-based search.

### embedding_poller
- **Trigger**: EventBridge Scheduler (every 1 minute)
- **Responsibility**: Queries DynamoDB for `processing` tasks, calls `GetAsyncInvoke` on Bedrock to check job status, parses JSONL output from S3, stores segment vectors (`content_id#seg0`, `#seg1`, ...) in S3 Vectors

### search
- **Trigger**: API Gateway `POST /api/search`
- **Responsibility**: Embeds query (text or file) via Bedrock sync API using modality-specific `embeddingPurpose` (with instruction prefix for text queries), queries S3 Vectors with optional user_id/modality filters. For audio/video filters, also queries `modality='transcript'` vectors to find matches in transcribed speech. Returns up to 3 best-matching segments per content item with time offset and transcript snippet.

### transcribe_poller
- **Trigger**: EventBridge Scheduler (every 1 minute)
- **Responsibility**: Queries DynamoDB for audio/video content with `transcribe_status=pending`, calls Amazon Transcribe `GetTranscriptionJob` to check completion, downloads transcript JSON from S3, chunks the transcript into ~500-character segments, generates text embeddings for each segment via Bedrock (`embed_text_sync`), stores them in S3 Vectors with keys `content_id#transcript#seg0`, `#transcript#seg1`, etc. Updates DynamoDB content record with `transcribe_status=completed` and full transcript text (up to 10k chars).

### task
- **Trigger**: API Gateway `GET /api/tasks`, `GET /api/tasks/{id}`
- **Responsibility**: Lists tasks for authenticated user from DynamoDB (PK=`USER#{user_id}`), batch-fetches content records to populate filenames

### vector_setup
- **Trigger**: CloudFormation Custom Resource (on stack create/delete)
- **Responsibility**: Creates S3 Vectors bucket and index (`content-embeddings`, 1024-dim cosine) during initial deployment

## DynamoDB Single-Table Design

**Table name**: `multimodal-retrieval-dev-content-dev`

### Key Schema

```
PK              SK                              GSI1PK              GSI1SK
USER#{user_id}  PROFILE                         -                   -
USER#{user_id}  CONTENT#{content_id}            CONTENT#{id}        METADATA
USER#{user_id}  TASK#{created_at}#{task_id}     TASK#{task_id}      DETAIL
```

### Entity: CONTENT

```
data: {
  filename, mime_type, modality, file_size,
  s3_key, s3_bucket,
  is_indexed: bool,
  created_at: ISO8601,
  // Audio/video only (populated by Transcribe pipeline):
  transcribe_status: "pending" | "completed" | "failed",
  transcribe_job_name: str,
  transcript: str  // full transcript text, max 10k chars
}
```

### Entity: TASK

```
data: {
  task_id, content_id, modality, filename,
  status: pending|processing|completed|failed,
  error_message?,
  created_at, updated_at: ISO8601
}
```

### Access Patterns

| Pattern | Key condition |
|---------|--------------|
| List user's tasks (chronological) | `PK=USER#{id}`, `SK begins_with TASK#` |
| Get task by task_id | `GSI1PK=TASK#{task_id}` |
| Get content metadata | `PK=USER#{id}`, `SK=CONTENT#{content_id}` |
| Get content by content_id | `GSI1PK=CONTENT#{content_id}` |

## Data Flow

### Upload → Embedding → Indexed

```
1. Frontend: POST /api/content/request-upload
   └─► Content Lambda generates S3 presigned POST URL (SigV4, KMS)

2. Frontend: PUT directly to S3 presigned URL

3. Frontend: POST /api/content/confirm-upload
   └─► Content Lambda:
       - Creates CONTENT record in DynamoDB (is_indexed=false)
       - Creates TASK record (status=pending)
       - Routes to SQS:
           Small file → EmbeddingQueue
           Large audio/video → LargeFileEmbeddingQueue

4. Embedding Lambda (SQS trigger):
   - Downloads file from S3
   - Calls Bedrock Nova MME InvokeModel (sync) with modality-specific embeddingPurpose
   - Stores vector in S3 Vectors as key=content_id
   - Updates CONTENT (is_indexed=true), TASK (status=completed)
   - For audio/video: starts Amazon Transcribe job, sets transcribe_status=pending in DynamoDB

   OR for large files:
4a. Large file handler (SQS trigger):
   - Calls Bedrock StartAsyncInvoke
   - Stores invocation_id in TASK (status=processing)

4b. EmbeddingPoller (EventBridge 1-min):
   - Checks GetAsyncInvoke status
   - On completion: parses JSONL from S3 output
   - Stores segment vectors: content_id#seg0, #seg1, ...
   - Updates TASK (status=completed)
```

### Search Flow

```
1. Frontend: POST /api/search {query_text or query_file, top_k, modality_filter?}

2. Search Lambda:
   a. Embed query via Bedrock Nova MME InvokeModel with modality-specific embeddingPurpose:
      - Text query for images/video/audio → GENERIC_RETRIEVAL
        with prefix "Instruction: Find an image, video or document that matches: Query: ..."
      - Text query for text/documents → TEXT_RETRIEVAL
        with prefix "Instruction: Given a query, retrieve relevant passages: Query: ..."
      - Image file query → IMAGE_RETRIEVAL
      - Audio file query → AUDIO_RETRIEVAL
      - Video file query → VIDEO_RETRIEVAL
   b. Call S3 Vectors QueryVectors with filter: {user_id: $eq, modality: $eq}
      For audio/video modality filter: also query modality='transcript' vectors separately
   c. Group results by content_id; keep up to 3 best segments per content item
   d. Batch-fetch CONTENT metadata from DynamoDB
   e. Generate S3 presigned download URLs (CloudFront signed URL if key configured)
   f. Return results sorted by similarity score descending, with segment metadata
      (segment_index, time_offset_seconds, duration_seconds, isTranscript, transcriptText)
```

## Async Embedding Architecture

```
Large file upload
      │
      ▼
LargeFileEmbeddingQueue (SQS, 60s visibility)
      │
      ▼
Embedding Lambda (large_file_handler)
  └─► StartAsyncInvoke → Bedrock
  └─► Save invocation_id to TASK record (status=processing)
  └─► Bedrock writes output to:
      s3://embeddings-output/{content_id}/{invocation_id}/
          segmented-embedding-result.json  (manifest)
          embedding-*.jsonl               (per segment, JSONL)
      │
      ▼
EventBridge Scheduler (every 1 min)
      │
      ▼
EmbeddingPoller Lambda
  └─► Query DynamoDB for status=processing tasks
  └─► GetAsyncInvoke → check completion
  └─► Parse manifest → get output file URIs
  └─► Parse JSONL → extract per-segment embeddings
  └─► PutVectors: content_id#seg0, #seg1, ... → S3 Vectors
  └─► Update CONTENT (is_indexed=true), TASK (status=completed)
```

## Transcribe Pipeline

Audio and video content gets a parallel transcription pipeline that enables text-based search of spoken content.

```
Embedding Lambda (audio/video SQS trigger)
  └─► Amazon Transcribe StartTranscriptionJob
  └─► DynamoDB: transcribe_status=pending, transcribe_job_name=...
  └─► Transcript output: s3://{content_bucket}/transcripts/{user_id}/{content_id}/transcript.json

EventBridge Scheduler (every 1 min)
  └─► TranscribePoller Lambda
      └─► Query DynamoDB for transcribe_status=pending content
      └─► GetTranscriptionJob → check completion
      └─► On COMPLETED:
          └─► Download transcript.json from S3
          └─► Chunk full transcript into ~500-char segments
          └─► For each segment: embed_text_sync (GENERIC_RETRIEVAL purpose)
          └─► PutVectors: content_id#transcript#seg0, #transcript#seg1, ... → S3 Vectors
              metadata: {user_id, modality='transcript', content_id}
          └─► DynamoDB: transcribe_status=completed, transcript=<full text, max 10k>
```

**Search integration**: When searching with `modality_filter: ["audio"]` or `["video"]`, the search handler also queries `modality='transcript'` vectors. Transcript matches carry `isTranscript=true` and a `transcriptText` snippet in the result.

**Frontend display**:
- "转录中..." badge: transcription is in progress
- "文字匹配" badge: result matched via transcript (not audio embedding)
- Transcript snippet shown inline; full transcript expandable

## Bedrock Nova MME API

**Model ID**: `amazon.nova-2-multimodal-embeddings-v1:0` (us-east-1)

**embeddingPurpose by use case**:

| Use | embeddingPurpose |
|-----|-----------------|
| Index image | `IMAGE_INDEX` |
| Index audio | `AUDIO_INDEX` |
| Index video | `VIDEO_INDEX` |
| Index text/document | `TEXT_INDEX` |
| Search query (images/video/audio) | `GENERIC_RETRIEVAL` (with instruction prefix) |
| Search query (text/documents) | `TEXT_RETRIEVAL` (with instruction prefix) |
| Search query (file: image) | `IMAGE_RETRIEVAL` |
| Search query (file: audio) | `AUDIO_RETRIEVAL` |
| Search query (file: video) | `VIDEO_RETRIEVAL` |
| Transcript segment index | `GENERIC_INDEX` |
| Transcript segment query | `GENERIC_RETRIEVAL` |

**Sync API** (InvokeModel) — for small files:
```json
{
  "taskType": "SINGLE_EMBEDDING",
  "singleEmbeddingParams": {
    "embeddingPurpose": "AUDIO_INDEX",   // modality-specific
    "embeddingDimension": 1024,
    "text": { "value": "..." }           // or
    "image": { "source": { "bytes": "..." }, "detailLevel": "STANDARD_IMAGE" }  // or
    "audio": { "source": { "bytes": "..." }, "format": "wav" }
  }
}
// Response: result.embeddings[0].embedding → [float × 1024]
```

**Async API** (StartAsyncInvoke) — for large audio/video (>30s or >100MB):
```json
{
  "taskType": "SEGMENTED_EMBEDDING",
  "segmentedEmbeddingParams": {
    "embeddingPurpose": "AUDIO_INDEX",   // modality-specific
    "embeddingDimension": 1024,
    "audio": { "source": { "s3Location": { "uri": "s3://..." } }, "format": "mp3" }
  }
}
// Output written to S3 as JSONL files
// Each line: {"embedding": [...], "status": "SUCCESS", "segmentMetadata": {"segmentIndex": 0, ...}}
```

## S3 Vectors

**Bucket**: `multimodal-retrieval-dev-vectors-dev` (us-west-2)
**Index**: `content-embeddings` (1024-dim, cosine)
**Filterable metadata**: `user_id`, `modality`, `content_id`

Vector keys:
- Single embedding: `{content_id}`
- Segmented (async Bedrock): `{content_id}#seg0`, `{content_id}#seg1`, ...
- Transcript segments: `{content_id}#transcript#seg0`, `{content_id}#transcript#seg1`, ...
  - metadata: `modality='transcript'` (allows filtering separately from audio/video embeddings)

Query with filter example:
```json
{
  "filter": {
    "$and": [
      {"user_id": {"$eq": "USER#..."}},
      {"modality": {"$eq": "audio"}}
    ]
  }
}
```
