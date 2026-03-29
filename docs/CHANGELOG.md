# Changelog

## 2026-03-28 — Phase 2: Amazon Transcribe Integration

### Feature: Speech-to-Text Search for Audio/Video

Audio and video files now go through a parallel transcription pipeline via Amazon Transcribe, enabling text-based semantic search on spoken content.

**What changed**:
1. **Embedding Lambda** (`embedding/handler.py`): After generating the audio/video embedding, starts an Amazon Transcribe job (`StartTranscriptionJob`) and sets `transcribe_status=pending` in the DynamoDB content record.
2. **New `TranscribePollerFunction`**: EventBridge Scheduler (1-minute) → polls for `transcribe_status=pending` content → calls `GetTranscriptionJob` → on completion downloads transcript JSON from S3 → chunks into ~500-char segments → generates text embeddings via Bedrock → stores in S3 Vectors as `content_id#transcript#seg0`, `#transcript#seg1`, etc. with `modality='transcript'`. Updates DynamoDB with `transcribe_status=completed` and full transcript text (max 10k chars).
3. **Search Lambda** (`search/handler.py`): When `modality_filter` includes `audio` or `video`, also queries `modality='transcript'` vectors. Transcript matches carry `isTranscript=true` with a `transcriptText` snippet.
4. **DynamoDB CONTENT records** gain three new fields: `transcribe_status`, `transcribe_job_name`, `transcript`.
5. **Frontend**: `ResultCard` shows "转录中..." badge (pending), "文字匹配" badge (transcript match), transcript text snippet, and expandable full transcript toggle.
6. **IAM**: `EmbeddingFunction`/`LargeFileEmbeddingFunction` get `transcribe:StartTranscriptionJob`. `TranscribePollerFunction` gets `transcribe:GetTranscriptionJob` + `bedrock:InvokeModel` + `s3vectors:PutVectors`. `ContentBucketPolicy` allows `transcribe.amazonaws.com` GetObject+PutObject with `aws:SourceAccount` condition.

**Measured improvement**: Audio query "佛学是怎么变成佛教的" similarity on matching audio: 0.197 (audio embedding only) → 0.545 (transcript match).

---

## 2026-03-28 — Phase 1: Search Quality Fixes

### Fix 17: Modality-Specific embeddingPurpose

**Problem**: Search results had mediocre similarity scores across all modalities even for clearly matching content.

**Root cause**: All indexing and query calls used `embeddingPurpose: "GENERIC_INDEX"` regardless of modality. Nova MME produces higher-quality embeddings when the purpose matches the actual content type and query intent.

**Fix**: Updated `bedrock_client.py` to use modality-specific purposes:

| Use | embeddingPurpose |
|-----|-----------------|
| Index image | `IMAGE_INDEX` |
| Index audio | `AUDIO_INDEX` |
| Index video | `VIDEO_INDEX` |
| Index text/document | `TEXT_INDEX` |
| Search query (images/video/audio) | `GENERIC_RETRIEVAL` |
| Search query (text/documents) | `TEXT_RETRIEVAL` |
| Search query (file: image) | `IMAGE_RETRIEVAL` |
| Search query (file: audio) | `AUDIO_RETRIEVAL` |
| Search query (file: video) | `VIDEO_RETRIEVAL` |

---

### Fix 18: Instruction Prefix for Text Queries

**Problem**: Text search queries lacked the instruction prefix recommended for Nova MME retrieval tasks, reducing cross-modal match quality.

**Fix**: Updated `search/handler.py` to prepend the appropriate instruction prefix to text queries before embedding:
- For images/audio/video target modalities:
  ```
  Instruction: Find an image, video or document that matches the following description:
  Query: {user_query}
  ```
- For text/document target modalities:
  ```
  Instruction: Given a query, retrieve passages that are relevant to the query:
  Query: {user_query}
  ```

---

## 2026-03-28 — Initial Deployment and Bug Fixes

### Fix 1: Initial Deployment — SAM Template Bugs (5 fixes)

**Problem**: First `sam deploy` failed with multiple CloudFormation errors.

**Fixes**:
1. `EmbeddingsOutputBucket` resource was missing from template — added S3 bucket definition
2. `LargeFileEmbeddingQueue` referenced but not declared — added SQS queue resource
3. `EmbeddingPollerFunction` EventBridge schedule had wrong resource type — fixed to `AWS::Scheduler::Schedule`
4. IAM policy for `EmbeddingFunction` missing S3 read permission on `ContentBucket` — added
5. `VectorSetupFunction` custom resource handler path was wrong — corrected to `vector_setup/handler.lambda_handler`

---

### Fix 2: Lambda Layer requirements.txt Path Error

**Problem**: SAM build succeeded but Lambda functions failed at runtime with `ModuleNotFoundError: No module named 'shared'`.

**Root cause**: `requirements.txt` for the shared layer was placed at the project root instead of inside the layer directory. SAM builds layers from `ContentUri: backend/layers/shared/`, so it looks for `requirements.txt` at `backend/layers/shared/requirements.txt`.

**Fix**: Moved `requirements.txt` to `backend/layers/shared/requirements.txt` with `boto3>=1.38.0` (boto3 1.38+ includes `start_async_invoke` / `get_async_invoke` methods on `bedrock-runtime`; the system boto3 1.34 does not).

---

### Fix 3: Frontend Auth Token — AccessToken vs IdToken

**Problem**: All authenticated API calls returned 401.

**Root cause**: The frontend was sending the Cognito `AccessToken` in the `Authorization` header, but the API Gateway Cognito Authorizer validates the **IdToken**. Additionally, the code was prepending "Bearer " prefix which the authorizer doesn't expect.

**Fix**: Updated `services/api.ts` to use `fetchAuthSession().tokens.idToken.toString()` (no "Bearer" prefix).

---

### Fix 4: snake_case / camelCase Mismatch

**Problem**: API responses returned camelCase field names (e.g., `taskId`, `contentId`) but frontend TypeScript types expected snake_case (`task_id`, `content_id`).

**Root cause**: Lambda functions returned Python dict keys directly without transformation; the frontend used different conventions.

**Fix**: Standardized all API responses to snake_case. Updated TypeScript `types/index.ts` to match.

---

### Fix 5: S3 Presigned POST Failing with 400 on Upload

**Problem**: Direct S3 uploads from the browser returned HTTP 400 "SignatureDoesNotMatch".

**Root cause**: `ContentBucket` uses KMS encryption (`aws/s3` managed key). KMS-encrypted S3 buckets require **SigV4** signatures. The `generate_presigned_post` call used the default boto3 client without `Config(signature_version="s3v4")`.

**Fix**: Added `Config(signature_version="s3v4")` to the S3 client used for presigned URL generation in `s3_client.py`.

---

### Fix 6: CloudFront Private Key 500 Error

**Problem**: Search results returned download URLs that pointed to CloudFront but the Lambda crashed with a 500 error when attempting to generate signed URLs.

**Root cause**: `ContentFunction` was missing `secretsmanager:GetSecretValue` permission for `CloudFrontPrivateKeySecret`.

**Fix**: Added the IAM permission to `ContentFunction`'s policy in `template.yaml`. Also added fallback: if no CloudFront key pair is configured (`CLOUDFRONT_KEY_PAIR_ID` empty), fall back to S3 presigned URLs automatically.

---

### Fix 7: DynamoDB Decimal Serialization Error

**Problem**: API responses with numeric fields (e.g., `file_size`) caused `TypeError: Object of type Decimal is not JSON serializable`.

**Root cause**: DynamoDB returns numbers as Python `Decimal` objects (not `int` or `float`). The `json.dumps()` call in the Lambda response helper didn't handle this.

**Fix**: Added a custom JSON encoder in the shared `api_response()` helper:
```python
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)
```

---

### Fix 8: Nova MME API Format (Critical)

**Problem**: All embedding calls failed with `ValidationException` from Bedrock.

**Root cause**: `amazon.nova-2-multimodal-embeddings-v1:0` was released in October 2025. The code used an incorrect API format copied from older embedding models.

**Incorrect format** (what was in the code):
```json
{
  "taskType": "RETRIEVAL",
  "singleEmbeddingParams": {
    "inputText": "...",
    "embeddingConfig": {"outputEmbeddingLength": 1024}
  }
}
```

**Correct format**:
```json
{
  "taskType": "SINGLE_EMBEDDING",
  "singleEmbeddingParams": {
    "embeddingPurpose": "GENERIC_INDEX",
    "embeddingDimension": 1024,
    "text": {"value": "..."}
  }
}
```

Additional format differences:
- Images require `detailLevel: "STANDARD_IMAGE"` field
- Audio/video must specify `format` (derived from MIME type)
- Response path: `result["embeddings"][0]["embedding"]` (not `result["embedding"]`)
- Async uses `taskType: "SEGMENTED_EMBEDDING"` + `segmentedEmbeddingParams`
- Async client is `bedrock-runtime`, not `bedrock`

**Fix**: Rewrote `bedrock_client.py` with correct API format for sync text, image, audio, video, and async segmented embeddings.

---

### Fix 9: S3 Vectors Index Never Created

**Problem**: Embedding Lambda failed with `NoSuchBucket` on first run; vector index did not exist.

**Root cause 1**: `VectorSetupFunction` custom resource was defined in `template.yaml` but the CloudFormation custom resource `ServiceToken` was pointing to the wrong Lambda ARN format.

**Root cause 2**: `metadataConfiguration` in `create_index` was using `filterableMetadataKeys` (not a real API parameter). The correct parameter is `nonFilterableMetadataKeys` — all keys not listed there are filterable by default.

**Fix**: Corrected the custom resource handler and index creation call. Manually created the index via boto3 during the debugging session. Stack now creates it correctly on fresh deploy.

---

### Fix 10: SQS Message Missing mime_type

**Problem**: Image files were being embedded as JPEG regardless of their actual format.

**Root cause**: The `_enqueue_embedding()` helper in `content/handler.py` was not including `mime_type` in the SQS message body. The Embedding Lambda defaulted to `"jpeg"` when the field was absent.

**Fix**: Added `mime_type` to the SQS message payload in `content/handler.py`.

---

### Fix 11: Audio >30s Incorrectly Sent to Sync Queue

**Problem**: Audio files longer than 30 seconds caused Bedrock `ValidationException` ("content too large for synchronous API").

**Root cause**: The routing logic in `content/handler.py` only checked `file_size > 100MB` for async routing, not audio duration.

**Fix**: Added duration detection: for audio/video files, if `duration_seconds > 30 OR file_size > 100MB`, route to `LargeFileEmbeddingQueue`.

---

### Fix 12: embeddingPurpose INDEX vs RETRIEVAL

**Problem**: Vectors were stored but search results had very low similarity scores (all < 0.3) even for obviously matching content.

**Root cause**: `embeddingPurpose: "GENERIC_INDEX"` must be used for **both** indexing and querying to ensure vectors are in the same embedding space. The search Lambda was using `embeddingPurpose: "GENERIC_RETRIEVAL"` for query vectors, producing vectors in a different space.

**Fix**: Changed all embedding calls (both indexing and search) to use `embeddingPurpose: "GENERIC_INDEX"`.

**Impact**: Vectors stored before this fix are in a different embedding space. Re-uploading content is required for accurate search results (see KNOWN_ISSUES.md).

---

### Fix 13: Task List Missing Filename

**Problem**: `GET /api/tasks` returned tasks with `filename: null`.

**Root cause**: The TASK DynamoDB record does not store `filename` directly. The filename lives in the CONTENT record. The task handler was returning task records without fetching the associated content.

**Fix**: Updated `task/handler.py` to extract `content_id` from each task's SK pattern (`TASK#{created_at}#{task_id}`), then perform a `batch_get_item` to fetch all CONTENT records in one DynamoDB call, and join the filename.

---

### Fix 14: PutVectors 500 — Batch Limit Exceeded

**Problem**: `EmbeddingPollerFunction` crashed with HTTP 500 when storing segment vectors for long audio files.

**Root cause**: S3 Vectors `put_vectors` has a maximum batch size of 500 vectors per call. Long audio files can produce hundreds of segments; the code was sending all segments in a single call.

**Fix**: Added batching in `s3_client.py`: chunk the vectors list into batches of 500 before calling `put_vectors`.

---

### Fix 15: S3 Vectors Query Filter Syntax

**Problem**: Search with `modality_filter` failed with `ValidationException: Invalid filter expression`.

**Root cause**: The filter used incorrect syntax `{"modality": "audio"}` (Equals-style) instead of the S3 Vectors operator syntax.

**Incorrect**:
```json
{"filter": {"modality": "audio"}}
```

**Correct**:
```json
{"filter": {"modality": {"$eq": "audio"}}}
```

**Fix**: Updated `s3_client.py` `query_vectors()` to use `$eq`, `$and` operators per S3 Vectors API spec.

---

### Fix 16: Segment-Level Search Results

**Problem**: Searching returned the same content item multiple times when it had multiple segments, and `similarity_score` was inconsistent.

**Root cause**: S3 Vectors returns one result per vector key. For segmented content (`content_id#seg0`, `#seg1`, ...), each segment was returned as a separate result. The frontend showed duplicate rows for the same file.

**Fix**: Added deduplication in `search/handler.py`:
- Group results by `content_id` (extract from key by splitting on `#`)
- Keep only the highest `similarity_score` per `content_id`
- Include `segment_index` in the response to indicate which segment matched

