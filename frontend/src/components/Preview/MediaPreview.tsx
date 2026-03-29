import { useState, useRef, useEffect } from 'react'
import ReactPlayer from 'react-player/lazy'
import type { Modality } from '@/types'

interface MediaPreviewProps {
  url: string
  modality: Modality | string
  filename: string
  seekTo?: number | null
}

function AudioPreview({ url, seekTo }: { url: string; seekTo?: number | null }) {
  const audioRef = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    if (seekTo != null && audioRef.current) {
      audioRef.current.currentTime = seekTo
      audioRef.current.play().catch(() => {})
    }
  }, [seekTo])

  return (
    <audio ref={audioRef} controls className="w-full h-10" preload="metadata">
      <source src={url} />
      <span className="text-sm text-gray-500">浏览器不支持音频播放</span>
    </audio>
  )
}

function VideoPreview({ url, seekTo }: { url: string; seekTo?: number | null }) {
  const playerRef = useRef<ReactPlayer>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (seekTo != null && playerRef.current) {
      playerRef.current.seekTo(seekTo, 'seconds')
    }
  }, [seekTo])

  return (
    <div>
      {expanded ? (
        <div className="relative aspect-video">
          <ReactPlayer
            ref={playerRef}
            url={url}
            controls
            width="100%"
            height="100%"
            config={{ file: { attributes: { controlsList: 'nodownload' } } }}
          />
          <button
            onClick={() => setExpanded(false)}
            className="absolute top-2 right-2 bg-black/50 text-white rounded-full w-6 h-6 flex items-center justify-center text-xs z-10"
          >
            ✕
          </button>
        </div>
      ) : (
        <button
          onClick={() => setExpanded(true)}
          className="w-full bg-gray-800 rounded-lg p-6 text-center text-white hover:bg-gray-700 transition-colors"
        >
          <span className="text-2xl block mb-1">▶</span>
          <span className="text-sm">点击播放视频</span>
        </button>
      )}
    </div>
  )
}

export function MediaPreview({ url, modality, filename, seekTo }: MediaPreviewProps) {
  const [expanded, setExpanded] = useState(false)
  const [imgError, setImgError] = useState(false)

  if (modality === 'image') {
    return (
      <div>
        {expanded ? (
          <div className="relative">
            {imgError ? (
              <div className="bg-gray-100 rounded-lg p-4 text-center text-gray-500 text-sm">图片预览不可用</div>
            ) : (
              <img
                src={url}
                alt={filename}
                className="w-full max-h-96 object-contain rounded-lg bg-gray-50"
                onError={() => setImgError(true)}
              />
            )}
            <button
              onClick={() => setExpanded(false)}
              className="absolute top-2 right-2 bg-black/50 text-white rounded-full w-6 h-6 flex items-center justify-center text-xs"
            >
              ✕
            </button>
          </div>
        ) : (
          <button
            onClick={() => setExpanded(true)}
            className="w-full text-left bg-gray-50 rounded-lg overflow-hidden hover:bg-gray-100 transition-colors"
          >
            {imgError ? (
              <div className="p-3 text-center text-gray-400 text-sm">点击查看图片</div>
            ) : (
              <img
                src={url}
                alt={filename}
                className="w-full h-24 object-cover"
                onError={() => setImgError(true)}
              />
            )}
          </button>
        )}
      </div>
    )
  }

  if (modality === 'audio') {
    return <AudioPreview url={url} seekTo={seekTo} />
  }

  if (modality === 'video') {
    return <VideoPreview url={url} seekTo={seekTo} />
  }

  if (modality === 'text') {
    return (
      <div>
        <div className={`bg-gray-50 rounded-lg p-3 text-sm text-gray-700 ${expanded ? '' : 'max-h-20 overflow-hidden'}`}>
          <p className="text-gray-400 italic">（文本内容预览需通过下载查看）</p>
        </div>
      </div>
    )
  }

  if (modality === 'document') {
    return (
      <div className="bg-gray-50 rounded-lg p-3 flex items-center gap-3">
        <span className="text-2xl">📄</span>
        <div>
          <p className="text-sm font-medium text-gray-700">{filename}</p>
          <a href={url} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 hover:underline">
            在新窗口打开
          </a>
        </div>
      </div>
    )
  }

  return null
}
