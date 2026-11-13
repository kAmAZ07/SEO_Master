import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'

const authApi = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  getCurrentUser: vi.fn(),
  updateProfile: vi.fn(),
  changePassword: vi.fn(),
}))

vi.mock('@/api/authAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/authAPI')>()),
  ...authApi,
}))

const notificationsApi = vi.hoisted(() => ({ fetchNotificationItems: vi.fn() }))
vi.mock('@/api/notificationsAPI', () => notificationsApi)

import { bindSessionEvents, createAppStore } from './store'
import { changePassword, loadUser, login, logout } from './slices/authSlice'
import { loadNotifications, markNotificationRead } from './slices/notificationsSlice'
import { startAudit, selectAudit } from './slices/auditSlice'
import { emitSessionEvent, getAccessToken, getRefreshToken, clearSessionTokens, PENDING_PROJECT_SAVE_KEY } from '@/api/session'
import type { AuditStatus } from '@/types/audit'

const user = (id: string) => ({ id, email: `${id}@example.com`, name: id, role: 'user' as const })
const loginResponse = (id: string) => ({ user: user(id), token: `token-${id}`, refreshToken: `refresh-${id}` })

const audit = (uid: string): AuditStatus => ({
  uid, id: uid, url: 'https://example.com', status: 'running', progress: 10, createdAt: '2026-11-10T00:00:00Z',
  score: null, issues: { passed: 0, warnings: 0, errors: 0 }, details: [],
})

const httpError = (status: number) =>
  new AxiosError('error', 'ERR', undefined, null, {
    status, statusText: String(status), headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
  })

describe('сессия и срезы', () => {
  let unbind: () => void

  const newStore = () => {
    const store = createAppStore()
    unbind = bindSessionEvents(store)
    return store
  }

  beforeEach(() => {
    localStorage.clear()
    clearSessionTokens()
    vi.clearAllMocks()
  })

  afterEach(() => {
    unbind?.()
  })

  it('наличие токена в localStorage не считается входом, пока сервер не подтвердил профиль', async () => {
    localStorage.setItem('token', 'stored')
    authApi.getCurrentUser.mockResolvedValue(user('u1'))
    const store = newStore()

    expect(store.getState().auth.sessionStatus).toBe('restoring')
    expect(store.getState().auth.isAuthenticated).toBe(false)

    await store.dispatch(loadUser())

    expect(store.getState().auth.sessionStatus).toBe('authenticated')
    expect(store.getState().auth.user?.id).toBe('u1')
    expect(store.getState().auth.isAuthenticated).toBe(true)
  })

  it('восстановление с недействительным токеном завершает сессию и стирает токен', async () => {
    localStorage.setItem('token', 'stale')
    authApi.getCurrentUser.mockRejectedValue(httpError(401))
    const store = newStore()

    await store.dispatch(loadUser())

    expect(store.getState().auth.sessionStatus).toBe('reauth_required')
    expect(store.getState().auth.sessionNotice).toBe('session_expired')
    expect(getAccessToken()).toBeNull()
  })

  it('временная ошибка при восстановлении сохраняет токен и позволяет повторить', async () => {
    localStorage.setItem('token', 'stored')
    authApi.getCurrentUser.mockRejectedValueOnce(httpError(503)).mockResolvedValueOnce(user('u1'))
    const store = newStore()

    await store.dispatch(loadUser())
    expect(store.getState().auth.sessionStatus).toBe('restore_failed')
    expect(getAccessToken()).toBe('stored')

    await store.dispatch(loadUser())
    expect(store.getState().auth.sessionStatus).toBe('authenticated')
  })

  it('поздний ответ профиля не воскрешает сессию после выхода', async () => {
    localStorage.setItem('token', 'stored')
    let resolveUser: (value: unknown) => void = () => undefined
    authApi.getCurrentUser.mockReturnValue(new Promise((resolve) => { resolveUser = resolve }))
    authApi.logout.mockResolvedValue(undefined)
    const store = newStore()

    const pending = store.dispatch(loadUser())
    await store.dispatch(logout())
    resolveUser(user('u1'))
    await pending

    expect(store.getState().auth.sessionStatus).toBe('anonymous')
    expect(store.getState().auth.user).toBeNull()
  })

  it('вход сохраняет access-токен, держит refresh-токен в памяти и подтверждает сессию', async () => {
    authApi.login.mockResolvedValue(loginResponse('u1'))
    const store = newStore()

    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))

    expect(store.getState().auth.sessionStatus).toBe('authenticated')
    expect(getAccessToken()).toBe('token-u1')
    expect(getRefreshToken()).toBe('refresh-u1')
    expect(localStorage.getItem('refreshToken')).toBeNull()
    expect(JSON.stringify(Object.entries(localStorage))).not.toContain('refresh-u1')
  })

  it('выход очищает все срезы, токены и отложенное сохранение проекта', async () => {
    authApi.login.mockResolvedValue(loginResponse('u1'))
    authApi.logout.mockResolvedValue(undefined)
    notificationsApi.fetchNotificationItems.mockResolvedValue([{ id: 'n1' }])
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))
    await store.dispatch(loadNotifications())
    store.dispatch(selectAudit('audit-1'))
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'audit-1' }))
    expect(store.getState().notifications.items).toHaveLength(1)

    await store.dispatch(logout())

    const state = store.getState()
    expect(state.auth.sessionStatus).toBe('anonymous')
    expect(state.auth.user).toBeNull()
    expect(state.notifications.items).toEqual([])
    expect(state.notifications.readIds).toEqual([])
    expect(state.audit.activeUid).toBeNull()
    expect(state.dashboard.projects).toEqual([])
    expect(getAccessToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
    expect(localStorage.getItem(PENDING_PROJECT_SAVE_KEY)).toBeNull()
    expect(authApi.logout).toHaveBeenCalledTimes(1)
  })

  it('выход завершается локально, даже если сервер недоступен', async () => {
    authApi.login.mockResolvedValue(loginResponse('u1'))
    authApi.logout.mockRejectedValue(httpError(503))
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))

    await store.dispatch(logout())

    expect(store.getState().auth.sessionStatus).toBe('anonymous')
    expect(getAccessToken()).toBeNull()
  })

  it('reauth_required: данные аккаунта очищаются, публичный отчёт остаётся', async () => {
    authApi.login.mockResolvedValue(loginResponse('u1'))
    notificationsApi.fetchNotificationItems.mockResolvedValue([{ id: 'n1' }])
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))
    await store.dispatch(loadNotifications())
    store.dispatch(selectAudit('public-1'))

    clearSessionTokens()
    emitSessionEvent({ type: 'session_ended', reason: 'reauth_required' })

    const state = store.getState()
    expect(state.auth.sessionStatus).toBe('reauth_required')
    expect(state.auth.sessionNotice).toBe('session_expired')
    expect(state.notifications.items).toEqual([])
    expect(state.audit.activeUid).toBe('public-1')
  })

  it('смена пароля с reauth_required завершает сессию и просит войти заново', async () => {
    authApi.login.mockResolvedValue(loginResponse('u1'))
    authApi.changePassword.mockResolvedValue({ success: true, reauth_required: true })
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))

    await store.dispatch(changePassword({ currentPassword: 'old', newPassword: 'new-password-1' }))

    expect(store.getState().auth.sessionStatus).toBe('reauth_required')
    expect(store.getState().auth.sessionNotice).toBe('password_changed')
    expect(store.getState().auth.user).toBeNull()
    expect(getAccessToken()).toBeNull()
  })

  it('вход под другим аккаунтом без выхода не оставляет данных прежнего', async () => {
    authApi.login.mockResolvedValueOnce(loginResponse('u1')).mockResolvedValueOnce(loginResponse('u2'))
    notificationsApi.fetchNotificationItems.mockResolvedValue([{ id: 'n1' }])
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))
    await store.dispatch(loadNotifications())
    expect(store.getState().notifications.items).toHaveLength(1)

    await store.dispatch(login({ email: 'u2@example.com', password: 'x' }))

    expect(store.getState().auth.user?.id).toBe('u2')
    expect(store.getState().notifications.items).toEqual([])
    expect(store.getState().notifications.ownerId).toBe('u2')
  })

  it('отметки «прочитано» хранятся отдельно для каждого аккаунта', async () => {
    authApi.login.mockResolvedValueOnce(loginResponse('u1')).mockResolvedValueOnce(loginResponse('u2'))
    authApi.logout.mockResolvedValue(undefined)
    notificationsApi.fetchNotificationItems.mockResolvedValue([{ id: 'n1' }])
    const store = newStore()

    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))
    store.dispatch(markNotificationRead('n1'))
    expect(store.getState().notifications.readIds).toEqual(['n1'])
    await store.dispatch(logout())

    await store.dispatch(login({ email: 'u2@example.com', password: 'x' }))
    expect(store.getState().notifications.readIds).toEqual([])

    await store.dispatch(logout())
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))
    expect(store.getState().notifications.readIds).toEqual(['n1'])
  })

  it('ответ уведомлений предыдущего аккаунта не попадает в состояние нового', async () => {
    authApi.login.mockResolvedValueOnce(loginResponse('u1')).mockResolvedValueOnce(loginResponse('u2'))
    authApi.logout.mockResolvedValue(undefined)
    let resolveItems: (value: unknown) => void = () => undefined
    notificationsApi.fetchNotificationItems.mockReturnValueOnce(new Promise((resolve) => { resolveItems = resolve }))
    const store = newStore()
    await store.dispatch(login({ email: 'u1@example.com', password: 'x' }))

    const pending = store.dispatch(loadNotifications())
    await store.dispatch(logout())
    await store.dispatch(login({ email: 'u2@example.com', password: 'x' }))
    resolveItems([{ id: 'secret-of-u1' }])
    await pending

    expect(store.getState().notifications.items).toEqual([])
  })

  it('поздний ответ запуска аудита после выхода не появляется в новом состоянии', async () => {
    authApi.logout.mockResolvedValue(undefined)
    const publicApi = await import('@/api/publicAuditAPI')
    let resolveAudit: (value: AuditStatus) => void = () => undefined
    vi.spyOn(publicApi, 'submitAuditRequest').mockReturnValue(new Promise((resolve) => { resolveAudit = resolve }))
    const store = newStore()

    const pending = store.dispatch(startAudit({ url: 'https://example.com' }))
    await store.dispatch(logout())
    resolveAudit(audit('late'))
    await pending

    expect(store.getState().audit.currentAudit).toBeNull()
    expect(store.getState().audit.activeUid).toBeNull()
  })
})
