import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import type { Modality } from '@/types'

const MODALITY_OPTIONS: { value: Modality; label: string }[] = [
  { value: 'text', label: '文本' },
  { value: 'image', label: '图片' },
  { value: 'audio', label: '音频' },
  { value: 'video', label: '视频' },
  { value: 'document', label: '文档' },
]

interface SearchBoxProps {
  onSearch: (params: {
    queryText?: string
    queryFile?: File
    topK: number
    modalityFilter: Modality[]
  }) => void
  isSearching: boolean
}

export function SearchBox({ onSearch, isSearching }: SearchBoxProps) {
  const [queryText, setQueryText] = useState('')
  const [queryFile, setQueryFile] = useState<File | null>(null)
  const [topK, setTopK] = useState(10)
  const [modalityFilter, setModalityFilter] = useState<Modality[]>([])
  const [mode, setMode] = useState<'text' | 'file'>('text')

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted.length > 0) setQueryFile(accepted[0])
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    maxFiles: 1,
    accept: {
      'image/*': [],
      'audio/*': [],
      'video/*': [],
    },
  })

  const toggleModality = (m: Modality) => {
    setModalityFilter((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m],
    )
  }

  const handleSearch = () => {
    if (mode === 'text' && !queryText.trim()) return
    if (mode === 'file' && !queryFile) return
    onSearch({
      queryText: mode === 'text' ? queryText : undefined,
      queryFile: mode === 'file' ? (queryFile ?? undefined) : undefined,
      topK,
      modalityFilter,
    })
  }

  return (
    <div className="card space-y-5">
      {/* Mode toggle */}
      <div className="flex gap-2">
        {(['text', 'file'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === m ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {m === 'text' ? '文本查询' : '文件查询'}
          </button>
        ))}
      </div>

      {/* Query input */}
      {mode === 'text' ? (
        <textarea
          value={queryText}
          onChange={(e) => setQueryText(e.target.value)}
          placeholder="输入查询文本，系统将检索语义相关的内容（支持跨模态检索）..."
          rows={3}
          className="input-field resize-none"
        />
      ) : (
        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
            isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400'
          }`}
        >
          <input {...getInputProps()} />
          {queryFile ? (
            <div>
              <p className="font-medium">{queryFile.name}</p>
              <p className="text-sm text-gray-500">{(queryFile.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
          ) : (
            <p className="text-gray-500">上传图片/音频作为查询条件</p>
          )}
        </div>
      )}

      {/* Filters row */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600 whitespace-nowrap">结果数量:</label>
          <input
            type="number"
            min={1}
            max={100}
            value={topK}
            onChange={(e) => setTopK(Math.max(1, Math.min(100, Number(e.target.value))))}
            className="w-20 input-field py-1"
          />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-gray-600">模态过滤:</span>
          {MODALITY_OPTIONS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => toggleModality(value)}
              className={`badge cursor-pointer transition-colors ${
                modalityFilter.includes(value)
                  ? 'bg-blue-100 text-blue-800'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={handleSearch}
        disabled={isSearching || (mode === 'text' ? !queryText.trim() : !queryFile)}
        className="btn-primary w-full"
      >
        {isSearching ? '检索中...' : '开始检索'}
      </button>
    </div>
  )
}
