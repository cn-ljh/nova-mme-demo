import { useState } from 'react'
import { SearchBox } from '@/components/Search/SearchBox'
import { ResultCard } from '@/components/Search/ResultCard'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { search } from '@/services/api'
import type { SearchResult, SearchState, Modality } from '@/types'

export function SearchPage() {
  const [state, setState] = useState<Pick<SearchState, 'status' | 'results' | 'processingTimeMs' | 'errorMessage'>>({
    status: 'idle',
    results: [],
  })

  const handleSearch = async (params: {
    queryText?: string
    queryFile?: File
    topK: number
    modalityFilter: Modality[]
  }) => {
    setState({ status: 'searching', results: [], errorMessage: undefined })
    try {
      const resp = await search(params)
      setState({
        status: 'done',
        results: resp.results,
        processingTimeMs: resp.processingTimeMs,
      })
    } catch (err: unknown) {
      setState({
        status: 'error',
        results: [],
        errorMessage: (err as { message?: string }).message ?? '检索失败，请重试',
      })
    }
  }

  const { status, results, processingTimeMs, errorMessage } = state

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">跨模态检索</h1>
        <p className="text-gray-500 mt-1">输入文本或上传文件，检索语义相关的多模态内容</p>
      </div>

      <SearchBox onSearch={handleSearch} isSearching={status === 'searching'} />

      {status === 'searching' && (
        <div className="flex justify-center py-8">
          <LoadingSpinner label="生成查询向量并检索中..." />
        </div>
      )}

      {status === 'error' && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">
          {errorMessage}
        </div>
      )}

      {status === 'done' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">
              检索结果 ({results.length} 条)
            </h2>
            {processingTimeMs && (
              <span className="text-sm text-gray-400">{processingTimeMs}ms</span>
            )}
          </div>

          {results.length === 0 ? (
            <div className="card text-center py-12 text-gray-400">
              <p className="text-3xl mb-2">🔍</p>
              <p>未找到相关内容，请尝试不同的查询</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {results.map((result: SearchResult, i) => (
                <ResultCard key={result.contentId} result={result} rank={i + 1} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
