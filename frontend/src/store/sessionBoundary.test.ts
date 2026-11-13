import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
const authApi = vi.hoisted(() => ({
  login: vi.fn(), register: vi.fn(), logout: vi.fn(), getCurrentUser: vi.fn(),
  updateProfile: vi.fn(), changePassword: vi.fn(),
}))
vi.mock('@/api/authAPI', async (original) => ({ ...(await original<typeof import('@/api/authAPI')>()), ...authApi }))
import { bindSessionEvents, createAppStore } from './store'
import { changePassword, loadUser, login, logout, register, updateProfile } from './slices/authSlice'
import { clearSessionTokens, endSession, getAccessToken, getSessionGeneration } from '@/api/session'
const user = (id: string) => ({ id, email: `${id}@example.com`, name: id, role: 'user' as const })
const response = (id: string) => ({ user: user(id), token: `token-${id}`, refreshToken: `refresh-${id}` })

describe('устаревшие auth completions', () => {
  let unbind: () => void
  beforeEach(() => { clearSessionTokens(); vi.resetAllMocks() })
  afterEach(() => { unbind(); clearSessionTokens() })

  it.each(['restore401', 'logout', 'password', 'profile', 'login', 'register'] as const)(
    '%s аккаунта A не меняет вошедший аккаунт B', async (kind) => {
      const store = createAppStore()
      unbind = bindSessionEvents(store)
      authApi.login.mockResolvedValueOnce(response('a'))
      await store.dispatch(login({ email: 'a@example.com', password: 'fixture' }))
      let release: (value: unknown) => void = () => undefined
      let reject: (error: Error) => void = () => undefined
      const delayed = new Promise((resolve, fail) => { release = resolve; reject = fail })
      let pending: Promise<unknown>
      if (kind === 'restore401') {
        authApi.getCurrentUser.mockReturnValueOnce(delayed)
        pending = store.dispatch(loadUser())
      } else if (kind === 'logout') {
        authApi.logout.mockReturnValueOnce(delayed)
        pending = store.dispatch(logout())
      } else if (kind === 'password') {
        authApi.changePassword.mockReturnValueOnce(delayed)
        pending = store.dispatch(changePassword({ currentPassword: 'old', newPassword: 'new' }))
      } else if (kind === 'profile') {
        authApi.updateProfile.mockReturnValueOnce(delayed)
        pending = store.dispatch(updateProfile({ name: 'from A' }))
      } else if (kind === 'login') {
        authApi.login.mockReturnValueOnce(delayed)
        pending = store.dispatch(login({ email: 'a@example.com', password: 'fixture' }))
      } else {
        authApi.register.mockReturnValueOnce(delayed)
        pending = store.dispatch(register({ email: 'a@example.com', password: 'fixture', name: 'A' }))
      }
      authApi.login.mockResolvedValueOnce(response('b'))
      await store.dispatch(login({ email: 'b@example.com', password: 'fixture' }))
      if (kind === 'restore401') {
        reject(new AxiosError('old 401', 'ERR', undefined, null, {
          status: 401, statusText: '401', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
        }))
      } else {
        release(kind === 'password' ? { success: true, reauth_required: true } : kind === 'profile' ? user('a') : response('a'))
      }
      await pending
      expect(getAccessToken()).toBe('token-b')
      expect(store.getState().auth.user?.id).toBe('b')
      expect(store.getState().auth.sessionStatus).toBe('authenticated')
    },
  )

  it('запоздалое fulfilled-событие не заменяет профиль новой сессии', async () => {
    const store = createAppStore()
    unbind = bindSessionEvents(store)
    const oldGeneration = getSessionGeneration()
    authApi.login.mockResolvedValueOnce(response('b'))
    await store.dispatch(login({ email: 'b@example.com', password: 'fixture' }))
    store.dispatch({ type: 'auth/updateProfile/fulfilled', payload: user('a'), meta: { sessionGeneration: oldGeneration } })
    expect(store.getState().auth.user?.id).toBe('b')
    endSession('logout')
  })
})
