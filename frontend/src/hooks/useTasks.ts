import { useState, useCallback } from 'react'
import { getTasks, getTask } from '@/services/api'
import type { TaskSummary, TaskDetail, TaskStatus } from '@/types'

interface UseTasksState {
  tasks: TaskSummary[]
  isLoading: boolean
  error: string | null
  nextToken?: string
  hasMore: boolean
}

export function useTasks(pageSize = 20) {
  const [state, setState] = useState<UseTasksState>({
    tasks: [],
    isLoading: false,
    error: null,
    nextToken: undefined,
    hasMore: false,
  })

  const fetchTasks = useCallback(async (statusFilter?: TaskStatus) => {
    setState((s) => ({ ...s, isLoading: true, error: null, tasks: [], nextToken: undefined }))
    try {
      const resp = await getTasks({ status: statusFilter, pageSize })
      setState({
        tasks: resp.tasks,
        isLoading: false,
        error: null,
        nextToken: resp.nextToken,
        hasMore: !!resp.nextToken,
      })
    } catch (err: unknown) {
      setState((s) => ({
        ...s,
        isLoading: false,
        error: (err as { message?: string }).message ?? 'Failed to load tasks',
      }))
    }
  }, [pageSize])

  const loadMore = useCallback(async (statusFilter?: TaskStatus) => {
    if (!state.nextToken) return
    setState((s) => ({ ...s, isLoading: true }))
    try {
      const resp = await getTasks({ status: statusFilter, pageSize, nextToken: state.nextToken })
      setState((s) => ({
        tasks: [...s.tasks, ...resp.tasks],
        isLoading: false,
        error: null,
        nextToken: resp.nextToken,
        hasMore: !!resp.nextToken,
      }))
    } catch (err: unknown) {
      setState((s) => ({
        ...s,
        isLoading: false,
        error: (err as { message?: string }).message ?? 'Failed to load more tasks',
      }))
    }
  }, [state.nextToken, pageSize])

  const refreshTask = useCallback(async (taskId: string): Promise<TaskDetail | null> => {
    try {
      const task = await getTask(taskId)
      setState((s) => ({
        ...s,
        tasks: s.tasks.map((t) =>
          t.taskId === taskId ? { ...t, ...task } : t,
        ),
      }))
      return task
    } catch {
      return null
    }
  }, [])

  return { ...state, fetchTasks, loadMore, refreshTask }
}
