import { useState, useEffect, useCallback } from 'react'
import { getCurrentUserProfile, login, logout, register } from '@/services/auth'
import type { UserProfile } from '@/types'

interface AuthState {
  user: UserProfile | null
  isLoading: boolean
  isAuthenticated: boolean
}

interface UseAuth extends AuthState {
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  signUp: (username: string, password: string, email: string) => Promise<void>
  refreshUser: () => Promise<void>
}

export function useAuth(): UseAuth {
  const [state, setState] = useState<AuthState>({
    user: null,
    isLoading: true,
    isAuthenticated: false,
  })

  const refreshUser = useCallback(async () => {
    try {
      const profile = await getCurrentUserProfile()
      setState({ user: profile, isLoading: false, isAuthenticated: !!profile })
    } catch {
      setState({ user: null, isLoading: false, isAuthenticated: false })
    }
  }, [])

  useEffect(() => {
    refreshUser()
  }, [refreshUser])

  const signIn = useCallback(async (username: string, password: string) => {
    await login(username, password)
    await refreshUser()
  }, [refreshUser])

  const signOut = useCallback(async () => {
    await logout()
    setState({ user: null, isLoading: false, isAuthenticated: false })
  }, [])

  const signUp = useCallback(async (username: string, password: string, email: string) => {
    await register(username, password, email)
  }, [])

  return { ...state, signIn, signOut, signUp, refreshUser }
}
