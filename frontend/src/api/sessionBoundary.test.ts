import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from './axiosConfig'
import { clearSessionTokens, endSession, getAccessToken, getRefreshToken, setSessionTokens, startSession } from './session'

const unauthorized = (config: InternalAxiosRequestConfig) =>
  new AxiosError('expired', 'ERR_BAD_REQUEST', config, null, {
    status: 401, statusText: '401', headers: {}, data: {}, config,
  })

describe('границы аккаунта при задержанных HTTP-ответах', () => {
  const originalAdapter = api.defaults.adapter
  beforeEach(() => {
    clearSessionTokens()
    setSessionTokens({ token: 'account-a', refreshToken: 'refresh-a' })
  })
  afterEach(() => {
    api.defaults.adapter = originalAdapter
    vi.restoreAllMocks()
    clearSessionTokens()
  })

  it.each(['logout', 'replacement', 'rejected'] as const)('поздний refresh: %s', async (boundary) => {
    api.defaults.adapter = (config) => Promise.reject(unauthorized(config))
    let release: (value: unknown) => void = () => undefined
    let reject: (error: Error) => void = () => undefined
    const refresh = vi.spyOn(axios, 'post').mockImplementation(
      () => new Promise((resolve, fail) => { release = resolve; reject = fail }),
    )
    const result = api.get('/private').catch((error: unknown) => error)
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
    if (boundary === 'logout') endSession('logout')
    else startSession({ token: 'account-b', refreshToken: 'refresh-b' })
    if (boundary === 'rejected') reject(unauthorized({} as InternalAxiosRequestConfig))
    else release({ data: { token: 'late-a', refreshToken: 'late-refresh-a' } })
    expect(await result).toMatchObject({ response: { status: 401 } })
    expect(getAccessToken()).toBe(boundary === 'logout' ? null : 'account-b')
    expect(getRefreshToken()).toBe(boundary === 'logout' ? null : 'refresh-b')
  })

  it.each(['local', 'external'] as const)('не повторяет старую мутацию после %s смены аккаунта', async (source) => {
    let release: () => void = () => undefined
    const requests: string[] = []
    api.defaults.adapter = (config) => {
      requests.push(String(config.headers.Authorization))
      if (requests.length === 1) {
        return new Promise((_resolve, reject) => { release = () => reject(unauthorized(config)) })
      }
      return Promise.resolve({ data: {}, status: 200, statusText: '200', headers: {}, config })
    }
    const result = api.patch('/auth/profile', { name: 'payload from A' }).catch((error: unknown) => error)
    await vi.waitFor(() => expect(requests).toEqual(['Bearer account-a']))
    if (source === 'local') startSession({ token: 'account-b', refreshToken: 'refresh-b' })
    else localStorage.setItem('token', 'account-b')
    release()
    expect(await result).toMatchObject({ response: { status: 401 } })
    expect(requests).toEqual(['Bearer account-a'])
  })

  it('отказ записи storage не возвращает токен предыдущего аккаунта', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => { throw new Error('storage unavailable') })
    startSession({ token: 'account-b', refreshToken: 'refresh-b' })
    expect(getAccessToken()).toBe('account-b')
    expect(getRefreshToken()).toBe('refresh-b')
  })

  it('штампует владельца запроса до асинхронной очереди Axios', async () => {
    const requests: string[] = []
    api.defaults.adapter = (config) => {
      requests.push(String(config.headers.Authorization))
      return Promise.resolve({ data: {}, status: 200, statusText: '200', headers: {}, config })
    }
    const result = api.patch('/auth/profile', { name: 'from A' }).catch((error: unknown) => error)
    startSession({ token: 'account-b', refreshToken: 'refresh-b' })
    expect(await result).toMatchObject({ code: 'ERR_CANCELED' })
    expect(requests).not.toContain('Bearer account-b')
  })

  it('старый refresh не объединяется с refresh новой сессии и не очищает его single-flight', async () => {
    api.defaults.adapter = (config) => config.headers.Authorization === 'Bearer new-b'
      ? Promise.resolve({ data: {}, status: 200, statusText: '200', headers: {}, config })
      : Promise.reject(unauthorized(config))
    const releases: Array<(value: unknown) => void> = []
    const refresh = vi.spyOn(axios, 'post').mockImplementation(() => new Promise((resolve) => { releases.push(resolve) }))
    const old = api.get('/private').catch((error: unknown) => error)
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
    startSession({ token: 'account-b', refreshToken: 'refresh-b' })
    const current = api.get('/private')
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
    releases[0]({ data: { token: 'late-a', refreshToken: 'late-refresh-a' } })
    await old
    const sibling = api.get('/private')
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(2))
    releases[1]({ data: { token: 'new-b', refreshToken: 'refresh-b2' } })
    expect((await Promise.all([current, sibling])).every((result) => result.status === 200)).toBe(true)
    expect(refresh).toHaveBeenCalledTimes(2)
    expect(getAccessToken()).toBe('new-b')
  })
})
