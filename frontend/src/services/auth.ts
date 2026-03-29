/**
 * Authentication service using AWS Amplify v6.
 * Uses SRP (Secure Remote Password) flow by default for security.
 */
import {
  signIn,
  signOut,
  signUp,
  getCurrentUser,
  fetchAuthSession,
  confirmSignUp,
  type SignInOutput,
} from 'aws-amplify/auth'
import type { UserProfile } from '@/types'

export async function register(
  username: string,
  password: string,
  email: string,
): Promise<{ userId: string; username: string }> {
  const result = await signUp({
    username,
    password,
    options: {
      userAttributes: { email },
    },
  })
  return {
    userId: result.userId ?? '',
    username,
  }
}

export async function login(username: string, password: string): Promise<SignInOutput> {
  return signIn({ username, password })
}

export async function logout(): Promise<void> {
  await signOut()
}

export async function getAccessToken(): Promise<string> {
  const session = await fetchAuthSession()
  return session.tokens?.accessToken?.toString() ?? ''
}

export async function getIdToken(): Promise<string> {
  const session = await fetchAuthSession()
  return session.tokens?.idToken?.toString() ?? ''
}

export async function getCurrentUserProfile(): Promise<UserProfile | null> {
  try {
    const user = await getCurrentUser()
    const session = await fetchAuthSession()
    const claims = session.tokens?.idToken?.payload ?? {}
    return {
      userId: user.userId,
      username: user.username,
      email: (claims['email'] as string) ?? '',
    }
  } catch {
    return null
  }
}

export async function confirmEmail(username: string, code: string): Promise<void> {
  await confirmSignUp({ username, confirmationCode: code })
}
