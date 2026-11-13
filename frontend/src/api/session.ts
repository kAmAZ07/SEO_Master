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
let generation = 0
let observedAccessToken: string | null = null
let memoryOnly = false

export const getAccessToken = (): string | null => {
  if (memoryOnly) return observedAccessToken
  try {
    const token = localStorage.getItem(ACCESS_TOKEN_KEY)
    if (token !== observedAccessToken) {
      // Another tab/storage writer replaced the principal; it is not our refresh rotation.
      generation += 1
      refreshToken = null
      observedAccessToken = token
    }
    return token
  } catch {
    return observedAccessToken
  }
}

export const getSessionGeneration = (): number => {
  getAccessToken()
  return generation
}

export const getRefreshToken = (): string | null => refreshToken

export const setSessionTokens = (tokens: { token: string; refreshToken?: string | null }): void => {
  observedAccessToken = tokens.token
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.token)
    memoryOnly = false
  } catch {
    memoryOnly = true
    // Хранилище недоступно (приватный режим): сессия живёт до перезагрузки страницы.
  }
  if (tokens.refreshToken) {
    refreshToken = tokens.refreshToken
  }
}

// A login is a new boundary, including login as the same user. Rotation is not.
export const startSession = (tokens: { token: string; refreshToken?: string | null }): void => {
  generation += 1
  refreshToken = null
  setSessionTokens(tokens)
}

export const clearSessionTokens = (): void => {
  generation += 1
  observedAccessToken = null
  refreshToken = null
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(PENDING_PROJECT_SAVE_KEY)
    memoryOnly = false
  } catch {
    memoryOnly = true
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
