// ============================================================
// API response types
// ============================================================

export type Modality = 'text' | 'image' | 'audio' | 'video' | 'document'
export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed'
export type TaskType = 'upload' | 'search'

export interface AuthTokens {
  idToken: string
  accessToken: string
  refreshToken: string
  expiresIn: number
}

export interface UserProfile {
  userId: string
  username: string
  email: string
}

export interface UploadRequest {
  filename: string
  mimeType: string
  fileSize: number
}

export interface PresignedUploadResponse {
  contentId: string
  uploadUrl: string
  uploadFields: Record<string, string>
  s3Key: string
  expiresIn: number
}

export interface ConfirmUploadResponse {
  taskId: string
  contentId: string
  modality: Modality
  status: TaskStatus
}

export interface TextUploadResponse {
  taskId: string
  contentId: string
  modality: 'text'
  status: TaskStatus
}

export interface ContentDetail {
  contentId: string
  userId: string
  modality: Modality
  filename: string
  fileSize: number
  mimeType: string
  s3Key: string
  s3Bucket: string
  isIndexed: boolean
  createdAt: string
  metadata: Record<string, unknown>
}

export interface SegmentMatch {
  segmentIndex: number
  similarityScore: number
  timeOffsetSeconds: number | null
  durationSeconds: number | null
  isTranscript?: boolean
  transcriptText?: string
}

export interface SearchResult {
  contentId: string
  bestScore: number
  modality: Modality
  filename: string
  fileSize: number
  previewUrl: string
  createdAt: string
  metadata: Record<string, unknown>
  segments: SegmentMatch[]
  transcript?: string
  transcribeStatus?: 'pending' | 'completed' | 'failed' | null
}

export interface SearchResponse {
  queryId: string
  results: SearchResult[]
  totalCount: number
  topK: number
  processingTimeMs: number
}

export interface TaskSummary {
  taskId: string
  taskType: TaskType
  modality: Modality
  status: TaskStatus
  createdAt: string
  updatedAt: string
  resultSummary?: string
  filename?: string | null
  fileSize?: number | null
}

export interface TaskDetail extends TaskSummary {
  contentId?: string
  errorMessage?: string
  processingTimeMs?: number
  downloadUrl?: string | null
}

export interface TaskListResponse {
  tasks: TaskSummary[]
  count: number
  pageSize: number
  nextToken?: string
}

export interface ApiError {
  errorCode: string
  message: string
  requestId: string
  details?: Record<string, unknown>
}

// ============================================================
// UI state types
// ============================================================

export interface UploadState {
  file: File | null
  text: string
  title: string
  status: 'idle' | 'uploading' | 'processing' | 'done' | 'error'
  progress: number
  taskId?: string
  errorMessage?: string
}

export interface SearchState {
  queryText: string
  queryFile: File | null
  topK: number
  modalityFilter: Modality[]
  status: 'idle' | 'searching' | 'done' | 'error'
  results: SearchResult[]
  processingTimeMs?: number
  errorMessage?: string
}
