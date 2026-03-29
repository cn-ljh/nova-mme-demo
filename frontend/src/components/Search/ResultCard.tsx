import { useState } from 'react'
import type { SearchResult, SegmentMatch } from '@/types'
import { MediaPreview } from '@/components/Preview/MediaPreview'

const MODALITY_EMOJI: Record<string, string> = {
  text: '📝', image: '🖼️', audio: '🎵', video: '🎬', document: '📄',
}

const MODALITY_LABEL: Record<string, string> = {
  text: '文本', image: '图片', audio: '音频', video: '视频', document: '文档',
}

interface ResultCardProps {
  result: SearchResult
  rank: number
}

function formatTimeOffset(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function ResultCard({ result, rank }: ResultCardProps) {
  const { bestScore, modality, filename, fileSize, previewUrl, createdAt, segments,
          transcript, transcribeStatus } = result
  const scorePercent = Math.round(bestScore * 100)
  const [seekTo, setSeekTo] = useState<number | null>(null)
  const [showTranscript, setShowTranscript] = useState(false)

  // Find the best-matching transcript segment (if any) for a text snippet
  const bestTranscriptSegment = segments.find(s => s.isTranscript && s.transcriptText)

  const formatSize = (bytes: number) => {
    if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
    if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${bytes} B`
  }

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
    } catch {
      return iso
    }
  }

  // Only Bedrock audio/video embedding segments (not transcript segments) are seekable
  const audioSegments = segments.filter(s => !s.isTranscript)
  const hasTimestamps = (modality === 'audio' || modality === 'video') &&
    audioSegments.some(s => s.timeOffsetSeconds != null)
  const isMultiSegment = audioSegments.length > 1

  return (
    <div className="card hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-gray-400 text-sm font-mono">#{rank}</span>
          <span className="text-lg">{MODALITY_EMOJI[modality] ?? '📄'}</span>
          <span className="badge bg-blue-50 text-blue-700">{MODALITY_LABEL[modality] ?? modality}</span>
          {isMultiSegment && (
            <span className="text-xs text-gray-400">{segments.length} 片段匹配</span>
          )}
          {transcribeStatus === 'pending' && (
            <span className="badge bg-yellow-50 text-yellow-700 text-xs">转录中...</span>
          )}
          {bestTranscriptSegment && (
            <span className="badge bg-green-50 text-green-700 text-xs">文字匹配</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div
            className="text-sm font-semibold px-2 py-0.5 rounded-full"
            style={{
              backgroundColor: `hsl(${scorePercent}, 70%, 90%)`,
              color: `hsl(${scorePercent}, 60%, 30%)`,
            }}
          >
            {scorePercent}% 相似
          </div>
        </div>
      </div>

      <p className="font-medium text-gray-800 mb-1 truncate" title={filename}>
        {filename}
      </p>
      <p className="text-xs text-gray-500 mb-3">
        {formatSize(fileSize)} · {formatDate(createdAt)}
      </p>

      {/* Score bar */}
      <div className="w-full bg-gray-100 rounded-full h-1.5 mb-4">
        <div
          className="h-1.5 rounded-full bg-gradient-to-r from-blue-400 to-blue-600 transition-all"
          style={{ width: `${scorePercent}%` }}
        />
      </div>

      {/* Segment timestamps for audio/video (Bedrock embedding segments only) */}
      {hasTimestamps && (
        <div className="mb-3">
          <p className="text-xs text-gray-500 mb-1.5">匹配片段：</p>
          <div className="flex flex-wrap gap-1.5">
            {audioSegments.map((seg: SegmentMatch) => {
              const offset = seg.timeOffsetSeconds!
              const segScore = Math.round(seg.similarityScore * 100)
              const isActive = seekTo === offset
              return (
                <button
                  key={seg.segmentIndex}
                  onClick={() => setSeekTo(offset)}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-mono border transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-blue-50 hover:border-blue-300'
                  }`}
                  title={`相似度 ${segScore}%`}
                >
                  <span>▶</span>
                  <span>{formatTimeOffset(offset)}</span>
                  <span className="text-xs opacity-70">{segScore}%</span>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Document segment index (no timestamps) */}
      {modality === 'document' && isMultiSegment && (
        <div className="mb-3">
          <p className="text-xs text-gray-500 mb-1.5">匹配段落：</p>
          <div className="flex flex-wrap gap-1.5">
            {audioSegments.map((seg: SegmentMatch) => (
              <span
                key={seg.segmentIndex}
                className="px-2 py-0.5 rounded-full text-xs bg-gray-50 text-gray-600 border border-gray-200"
              >
                段落 {seg.segmentIndex + 1} · {Math.round(seg.similarityScore * 100)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Matched transcript snippet */}
      {bestTranscriptSegment && (
        <div className="mb-3 p-2 bg-green-50 border border-green-200 rounded-lg">
          <p className="text-xs text-green-600 font-medium mb-1">匹配片段文字：</p>
          <p className="text-xs text-gray-700 line-clamp-3 leading-relaxed">
            {bestTranscriptSegment.transcriptText}
          </p>
        </div>
      )}

      {/* Full transcript toggle */}
      {transcript && (
        <div className="mb-3">
          <button
            onClick={() => setShowTranscript(!showTranscript)}
            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            {showTranscript ? '收起转录文字 ▲' : '查看完整转录文字 ▼'}
          </button>
          {showTranscript && (
            <div className="mt-2 p-2 bg-gray-50 border border-gray-200 rounded text-xs text-gray-700 max-h-40 overflow-y-auto leading-relaxed whitespace-pre-wrap">
              {transcript}
            </div>
          )}
        </div>
      )}

      {/* Media preview */}
      <MediaPreview url={previewUrl} modality={modality} filename={filename} seekTo={seekTo} />

      <div className="mt-3 flex justify-end">
        <a
          href={previewUrl}
          download={filename}
          className="btn-secondary text-sm py-1.5"
        >
          下载
        </a>
      </div>
    </div>
  )
}
