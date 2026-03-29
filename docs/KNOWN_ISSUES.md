# Known Issues and Limitations

## KI-1: Vectors Stored with Old embeddingPurpose May Have Degraded Search Quality

**Severity**: Medium (affects search quality for older content)

**Description**: Content indexed before the Phase 1 search quality fixes (2026-03-28) was embedded with `embeddingPurpose: "GENERIC_INDEX"` for all modalities. Current code uses modality-specific purposes (`IMAGE_INDEX`, `AUDIO_INDEX`, etc.) which produce better-matched embeddings. Old vectors remain in the index in a slightly different embedding sub-space.

**Affected content**: Any content indexed before the Phase 1 fix (Fix 17).

**Workaround**: Delete and re-upload affected content. There is no re-indexing API yet (see KI-4).

**Detection**: Search results with similarity scores consistently below 0.5 for content you expect to match.

---

## KI-2: segment_duration Not Stored for Older Vectors

**Severity**: Low (metadata only)

**Description**: Segmented embeddings (async Bedrock jobs for audio/video >30s) stored segment vectors as `content_id#seg0`, `#seg1`, etc. Earlier versions did not store `segment_duration_seconds` in the vector metadata.

**Impact**: The search response includes `segment_index` but cannot calculate the exact timestamp of the matching segment for older content.

**Workaround**: Re-upload content to get accurate segment duration metadata.

---

## KI-3: CloudFront Signed URLs Not Configured [RESOLVED]

**Severity**: ~~Medium~~ → RESOLVED (2026-03-29)

**Description**: Content download URLs fall back to S3 presigned URLs (15-minute expiry, direct S3 domain) instead of CloudFront signed URLs (1-hour expiry, CloudFront domain).

**Root cause**: CloudFront OAC with AWS-managed KMS key (`aws/s3`) cannot work without a KMS key policy grant. The CloudFront distribution cannot decrypt S3 objects encrypted with the managed key. Additionally, no CloudFront RSA key pair has been configured (`CloudFrontKeyPairId` parameter is empty).

**Impact**:
- Download URLs expose the S3 bucket domain instead of the CloudFront domain
- URLs expire in 15 minutes instead of 1 hour
- No CloudFront-level access control on content downloads

**Resolution options**:
1. Replace `aws/s3` managed key with a customer-managed KMS key and add CloudFront OAC as a key user
2. Or: Generate a CloudFront RSA key pair, store the private key in Secrets Manager, and pass the key pair ID as `CloudFrontKeyPairId` parameter on deploy

**Resolution (2026-03-29)**: Changed S3 bucket encryption from `aws:kms` to `AES256` (SSE-S3). CloudFront OAC can now decrypt S3 objects without KMS key policy. CloudFront key pair configuration for signed URLs is still pending — content downloads still use S3 presigned URLs as fallback.

---

## KI-4: No Re-indexing / Re-embedding Capability

**Severity**: Medium

**Description**: There is no API endpoint or admin tool to trigger re-embedding for existing content without deleting and re-uploading.

**Workaround**: Delete the content via `DELETE /api/content/{id}` and re-upload the original file.

**Future work**: Add `POST /api/content/{id}/reindex` endpoint that enqueues a re-embedding job without requiring file re-upload.

---

## KI-5: Lambda Cold Start Latency on Search

**Severity**: Low

**Description**: `SearchFunction` has a cold start time of approximately 400–700ms. Combined with Bedrock embedding latency (~200ms), the first search after a period of inactivity can take 1–2 seconds.

**Cause**: Lambda function loading the shared layer + boto3 clients on first invocation.

**Mitigation options**:
- Enable Lambda Provisioned Concurrency for `SearchFunction` (increases cost)
- Use Lambda SnapStart (requires Java runtime — not applicable here)

---

## KI-6: deploy-frontend.sh CloudFront Invalidation Bug

**Severity**: Low (dev workflow only)

**Description**: `scripts/deploy-frontend.sh` has a broken regex for parsing the CloudFront distribution ID from the domain name. The invalidation step silently fails.

**Workaround**:
```bash
DISTRIBUTION_ID=$(aws cloudfront list-distributions --region us-east-1 \
    --query "DistributionList.Items[?DomainName=='<YOUR_CLOUDFRONT_DOMAIN>'].Id" \
    --output text)
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*"
```

---

## KI-7: Processing Tasks May Be Stale

**Severity**: Low

**Description**: Tasks with `status=processing` represent async Bedrock jobs being polled by `EmbeddingPollerFunction`. If a Bedrock async job fails silently (job ID becomes invalid), the task stays in `processing` state indefinitely. The poller does not currently implement a maximum age timeout for processing tasks.

**Workaround**: Manually update the task status in DynamoDB or delete and re-upload.

**Future work**: Add a maximum processing age (e.g., 24 hours) after which tasks are automatically marked `failed`.

---

## KI-9: Audio/Video Uploaded Before Transcribe Integration Has No Transcript

**Severity**: Low (missing feature for old content)

**Description**: The Amazon Transcribe pipeline was added in Phase 2 (2026-03-28). Audio and video files uploaded before this change do not have `transcribe_status`, `transcribe_job_name`, or `transcript` fields in their DynamoDB records, and no transcript vectors exist in S3 Vectors for them. Text queries that would match their spoken content will not find them via the transcript search path.

**Affected content**: All audio/video content uploaded before the Phase 2 deployment.

**Workaround**: Delete and re-upload affected audio/video files. The Transcribe job will be started automatically on next embedding.

**Future work**: Add `POST /api/content/{id}/reindex` endpoint (see KI-4) to re-trigger transcription without re-uploading.

---

## KI-10: Amazon Transcribe Language Detection Limitations

**Severity**: Low

**Description**: Amazon Transcribe is invoked without an explicit language code, relying on automatic language detection. Automatic detection can fail or produce low-quality transcripts for:
- Very short audio clips (< 15 seconds)
- Audio with significant background noise or music
- Code-switching (mixed-language audio, e.g., Chinese with English technical terms)
- Languages not supported by Transcribe's auto-detection

**Impact**: Transcript text may be garbled or empty. Transcript-based search will return no results for that content, but audio/video embedding search still works normally.

**Workaround**: None currently. Manual language override is not exposed via the API.

**Future work**: Add an optional `language_code` field to the upload API that is passed to `StartTranscriptionJob`.

---

## KI-8: No Pagination on Content List

**Severity**: Low

**Description**: `GET /api/content` returns all content for the user in a single DynamoDB query with no pagination. For users with many files, this could be slow and produce large responses.

**Current limit**: DynamoDB query returns up to 1MB of data per call. With typical content records (~500 bytes each), this is approximately 2,000 items before pagination is needed.

---

## Stack Reference

After deploying, retrieve your stack values via:

```bash
aws cloudformation describe-stacks \
  --stack-name multimodal-retrieval-dev \
  --query 'Stacks[0].Outputs'
```

| Resource | Description |
|----------|-------------|
| API Gateway | `ApiUrl` CloudFormation output |
| CloudFront | `CloudFrontDomain` CloudFormation output |
| Cognito User Pool | `UserPoolId` CloudFormation output |
| Cognito Client ID | `UserPoolClientId` CloudFormation output |
| Content Bucket | `ContentBucketName` CloudFormation output |
| Vector Bucket | `VectorBucketName` CloudFormation output |
| Vector Index | `content-embeddings` (1024-dim cosine) |
| Bedrock Region | `us-east-1` (Nova MME availability) |
