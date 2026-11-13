import { combineReducers, configureStore, type Reducer, type UnknownAction } from '@reduxjs/toolkit'
import auditReducer from './slices/auditSlice'
import hitlReducer from './slices/hitlSlice'
import dashboardReducer from './slices/dashboardSlice'
import authReducer, { login, register } from './slices/authSlice'
import notificationsReducer from './slices/notificationsSlice'
import { sessionEnded, tokenRefreshed } from './sessionActions'
import { getSessionGeneration, subscribeSession } from '@/api/session'

const appReducer = combineReducers({
  audit: auditReducer,
  hitl: hitlReducer,
  dashboard: dashboardReducer,
  auth: authReducer,
  notifications: notificationsReducer,
})

type AppState = ReturnType<typeof appReducer>

// Корневой редьюсер очищает данные аккаунта при завершении сессии и при смене пользователя.
// Публичный отчёт (срез audit) не принадлежит аккаунту: при истёкшей сессии он остаётся,
// а при явном выходе очищается вместе со всем остальным.
export const rootReducer: Reducer<AppState, UnknownAction> = (state, action) => {
  const meta = action.meta
  if (action.type.startsWith('auth/') && meta && typeof meta === 'object' &&
      'sessionGeneration' in meta && typeof meta.sessionGeneration === 'number' &&
      meta.sessionGeneration !== getSessionGeneration()) {
    return state ?? appReducer(undefined, { type: '@@session/ignored' })
  }
  if (sessionEnded.match(action)) {
    const keepAudit = action.payload.reason !== 'logout' && state ? { audit: state.audit } : {}
    return appReducer({ ...keepAudit } as AppState | undefined, action)
  }

  if ((login.fulfilled.match(action) || register.fulfilled.match(action)) && state?.auth.user) {
    if (state.auth.user.id !== action.payload.user.id) {
      // Вход под другим аккаунтом без выхода: ничего от прежнего аккаунта показывать нельзя.
      return appReducer({ auth: state.auth } as AppState, action)
    }
  }

  return appReducer(state, action)
}

export const bindSessionEvents = (target: { dispatch: (action: UnknownAction) => unknown }): (() => void) =>
  subscribeSession((event) => {
    if (event.type === 'session_ended') {
      target.dispatch(sessionEnded({ reason: event.reason }))
    } else {
      target.dispatch(tokenRefreshed({ token: event.token }))
    }
  })

export const createAppStore = (preloadedState?: Partial<AppState>) =>
  configureStore({ reducer: rootReducer, preloadedState: preloadedState as AppState | undefined })

export const store = createAppStore()
bindSessionEvents(store)

export type RootState = AppState
export type AppDispatch = typeof store.dispatch
