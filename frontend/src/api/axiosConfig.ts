import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { endSession, emitSessionEvent, getAccessToken, getRefreshToken, setSessionTokens } from './session'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

type RetriableConfig = InternalAxiosRequestConfig & { _retriedAfterRefresh?: boolean }

// Эти запросы сами работают с учётными данными: 401 на них не означает «токен устарел».
const AUTH_ENDPOINTS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout']

const isAuthEndpoint = (url: string | undefined): boolean =>
  AUTH_ENDPOINTS.some((endpoint) => (url ?? '').includes(endpoint))

// Статусы, при которых сервер отверг сам refresh-токен: нужен повторный вход.
// Остальное (503 без Redis, сеть, таймаут) временно: сессию не рвём.
const REFRESH_REJECTED_STATUSES = new Set([400, 401, 403, 422])

let refreshInFlight: Promise<string> | null = null

const requestNewTokens = async (): Promise<string> => {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    endSession('reauth_required')
    throw new Error('Сессия истекла: нет refresh-токена.')
  }

  try {
    // Отдельный вызов axios без перехватчиков: иначе 401 на refresh зациклит обновление.
    const response = await axios.post('/api/auth/refresh', { refresh_token: refreshToken }, { timeout: 30000 })
    const token: unknown = response.data?.token
    const nextRefresh: unknown = response.data?.refreshToken
    if (typeof token !== 'string' || !token || typeof nextRefresh !== 'string' || !nextRefresh) {
      endSession('reauth_required')
      throw new Error('Сервер вернул некорректный ответ при обновлении сессии.')
    }
    setSessionTokens({ token, refreshToken: nextRefresh })
    emitSessionEvent({ type: 'tokens_refreshed', token })
    return token
  } catch (error) {
    if (axios.isAxiosError(error) && REFRESH_REJECTED_STATUSES.has(error.response?.status ?? 0)) {
      endSession('reauth_required')
    }
    throw error
  }
}

// Одновременные 401 ждут один общий запрос: refresh-токен одноразовый (ротация),
// второй параллельный запрос с тем же токеном отозвал бы всю сессию.
export const refreshAccessToken = (): Promise<string> => {
  if (!refreshInFlight) {
    refreshInFlight = requestNewTokens().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

const bearerOf = (config: InternalAxiosRequestConfig): string | null => {
  const header = config.headers?.Authorization
  return typeof header === 'string' && header.startsWith('Bearer ') ? header.slice(7) : null
}

api.interceptors.request.use(
  (config) => {
    const token = getAccessToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    const sentToken = config ? bearerOf(config) : null

    // 401 на запрос без токена (публичный отчёт и т.п.) — обычная ошибка: сессии, которую можно
    // обновить или разрушить, здесь нет.
    if (
      !axios.isAxiosError(error) ||
      error.response?.status !== 401 ||
      !config ||
      !sentToken ||
      config._retriedAfterRefresh ||
      isAuthEndpoint(config.url)
    ) {
      return Promise.reject(error)
    }

    config._retriedAfterRefresh = true
    let nextToken: string
    try {
      // Если токен уже обновили, пока этот запрос был в пути, повторяем с текущим токеном.
      const currentToken = getAccessToken()
      nextToken = currentToken && currentToken !== sentToken ? currentToken : await refreshAccessToken()
    } catch {
      // Обновить не удалось: вызывающий код видит исходный 401, а не ошибку refresh.
      return Promise.reject(error)
    }
    config.headers.Authorization = `Bearer ${nextToken}`
    return api.request(config)
  }
)

export default api
