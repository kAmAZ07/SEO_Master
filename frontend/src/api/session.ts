// Хранилище токенов сессии и канал событий между HTTP-слоем и Redux.
//
// Access-токен остаётся в localStorage (как и раньше), refresh-токен живёт только в памяти
// страницы: после перезагрузки восстановить сессию можно, пока access-токен не истёк.
// Перенос refresh в HttpOnly-cookie требует изменений на сервере и в этот модуль не входит.

export const ACCESS_TOKEN_KEY = 'token'
export const PENDING_PROJECT_SAVE_KEY = 'seoMaster.pendingProjectSave'

export type SessionEndReason = 'logout' | 'reauth_required' | 'password_changed'

export type SessionEvent =
  | { type: 'tokens_refreshed'; token: string }
  | { type: 'session_ended'; reason: SessionEndReason }

type SessionListener = (event: SessionEvent) => void

let refreshToken: string | null = null
const listeners = new Set<SessionListener>()

export const getAccessToken = (): string | null => {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY)
  } catch {
    return null
  }
}

export const getRefreshToken = (): string | null => refreshToken

export const setSessionTokens = (tokens: { token: string; refreshToken?: string | null }): void => {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.token)
  } catch {
    // Хранилище недоступно (приватный режим): сессия живёт до перезагрузки страницы.
  }
  if (tokens.refreshToken) {
    refreshToken = tokens.refreshToken
  }
}

export const clearSessionTokens = (): void => {
  refreshToken = null
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(PENDING_PROJECT_SAVE_KEY)
  } catch {
    // Нечего очищать, если хранилище недоступно.
  }
}

export const subscribeSession = (listener: SessionListener): (() => void) => {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export const emitSessionEvent = (event: SessionEvent): void => {
  listeners.forEach((listener) => listener(event))
}

// Завершает сессию: токены стираются до уведомления подписчиков, чтобы редьюсеры,
// пересоздающие начальное состояние, не увидели старый токен.
export const endSession = (reason: SessionEndReason): void => {
  clearSessionTokens()
  emitSessionEvent({ type: 'session_ended', reason })
}
