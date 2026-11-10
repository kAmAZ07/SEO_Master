import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import api from './axiosConfig'
import { clearSessionTokens, getAccessToken, getRefreshToken, setSessionTokens, subscribeSession, type SessionEvent } from './session'

const respond = (config: InternalAxiosRequestConfig, status: number, data: unknown = {}) => {
  const response = { data, status, statusText: String(status), headers: {}, config }
  if (status >= 400) {
    return Promise.reject(new AxiosError(`HTTP ${status}`, 'ERR_BAD_REQUEST', config, null, response))
  }
  return Promise.resolve(response)
}

// Сервер принимает только токен «new»: любой другой даёт 401.
const installServer = (calls: string[]) => {
  api.defaults.adapter = (config) => {
    const bearer = String(config.headers.Authorization ?? '')
    calls.push(`${config.url} ${bearer}`)
    if (config.url === '/private' && bearer !== 'Bearer new') {
      return respond(config, 401)
    }
    return respond(config, 200, { ok: true })
  }
}

describe('HTTP-слой: refresh и завершение сессии', () => {
  let events: SessionEvent[]
  let unsubscribe: () => void
  const originalAdapter = api.defaults.adapter

  beforeEach(() => {
    events = []
    unsubscribe = subscribeSession((event) => events.push(event))
    clearSessionTokens()
  })

  afterEach(() => {
    unsubscribe()
    api.defaults.adapter = originalAdapter
    vi.restoreAllMocks()
    clearSessionTokens()
  })

  it('одновременные 401 запускают один refresh и повторяют запросы с новым токеном', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    const calls: string[] = []
    installServer(calls)
    let release: (value: unknown) => void = () => undefined
    const refresh = vi.spyOn(axios, 'post').mockImplementation(
      () => new Promise((resolve) => { release = resolve }),
    )

    const first = api.get('/private')
    const second = api.get('/private')
    const third = api.get('/private')
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
    release({ data: { token: 'new', refreshToken: 'refresh-2' } })
    const results = await Promise.all([first, second, third])

    expect(results.every((result) => result.status === 200)).toBe(true)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(refresh).toHaveBeenCalledWith('/api/auth/refresh', { refresh_token: 'refresh-1' }, expect.anything())
    expect(getAccessToken()).toBe('new')
    // Refresh-токен одноразовый: после ротации в памяти лежит новый.
    expect(getRefreshToken()).toBe('refresh-2')
    expect(calls.filter((call) => call === '/private Bearer new')).toHaveLength(3)
    expect(events).toEqual([{ type: 'tokens_refreshed', token: 'new' }])
  })

  it('запрос, отправленный со старым токеном после обновления, повторяется без нового refresh', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    const calls: string[] = []
    // Ответ 401 приходит уже после того, как токен обновили другим запросом.
    api.defaults.adapter = async (config) => {
      calls.push(`${config.url} ${String(config.headers.Authorization)}`)
      if (config.url === '/slow' && config.headers.Authorization === 'Bearer old') {
        setSessionTokens({ token: 'new', refreshToken: 'refresh-2' })
        return respond(config, 401)
      }
      return respond(config, 200)
    }
    const refresh = vi.spyOn(axios, 'post')

    const response = await api.get('/slow')

    expect(response.status).toBe(200)
    expect(refresh).not.toHaveBeenCalled()
    expect(calls).toEqual(['/slow Bearer old', '/slow Bearer new'])
  })

  it('отклонённый refresh завершает сессию и отдаёт исходный 401, не меняя адрес страницы', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    installServer([])
    vi.spyOn(axios, 'post').mockRejectedValue(
      new AxiosError('rejected', 'ERR_BAD_REQUEST', undefined, null, {
        status: 401, statusText: '401', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
      }),
    )
    const before = window.location.href

    await expect(api.get('/private')).rejects.toMatchObject({ response: { status: 401 } })

    expect(events).toEqual([{ type: 'session_ended', reason: 'reauth_required' }])
    expect(getAccessToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
    expect(window.location.href).toBe(before)
  })

  it('без refresh-токена в памяти (после перезагрузки) устаревший токен завершает сессию', async () => {
    localStorage.setItem('token', 'old')
    installServer([])
    const refresh = vi.spyOn(axios, 'post')

    await expect(api.get('/private')).rejects.toMatchObject({ response: { status: 401 } })

    expect(refresh).not.toHaveBeenCalled()
    expect(events).toEqual([{ type: 'session_ended', reason: 'reauth_required' }])
  })

  it('временная недоступность refresh (503) не разрывает сессию', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    installServer([])
    vi.spyOn(axios, 'post').mockRejectedValue(
      new AxiosError('unavailable', 'ERR_BAD_RESPONSE', undefined, null, {
        status: 503, statusText: '503', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
      }),
    )

    await expect(api.get('/private')).rejects.toMatchObject({ response: { status: 401 } })

    expect(events).toEqual([])
    expect(getAccessToken()).toBe('old')
    expect(getRefreshToken()).toBe('refresh-1')
  })

  it('401 на запрос без токена — обычная ошибка: refresh не вызывается, сессия не затрагивается', async () => {
    installServer([])
    const refresh = vi.spyOn(axios, 'post')

    await expect(api.get('/private')).rejects.toMatchObject({ response: { status: 401 } })

    expect(refresh).not.toHaveBeenCalled()
    expect(events).toEqual([])
  })

  it('401 на вход не запускает refresh', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    api.defaults.adapter = (config) => respond(config, 401)
    const refresh = vi.spyOn(axios, 'post')

    await expect(api.post('/auth/login', {})).rejects.toMatchObject({ response: { status: 401 } })

    expect(refresh).not.toHaveBeenCalled()
    expect(events).toEqual([])
  })

  it('повторный запрос, упавший не по 401, отдаёт свою ошибку, а не исходный 401', async () => {
    setSessionTokens({ token: 'old', refreshToken: 'refresh-1' })
    api.defaults.adapter = (config) =>
      config.headers.Authorization === 'Bearer old' ? respond(config, 401) : respond(config, 500)
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { token: 'new', refreshToken: 'refresh-2' } })

    await expect(api.get('/private')).rejects.toMatchObject({ response: { status: 500 } })
  })
})
