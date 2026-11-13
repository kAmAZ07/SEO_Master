import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import axios from 'axios'
import type { User, LoginCredentials, RegisterData } from '@/types/user'
import {
  login as loginAPI,
  register as registerAPI,
  logout as logoutAPI,
  getCurrentUser,
  updateProfile as updateProfileAPI,
  changePassword as changePasswordAPI,
  getApiErrorMessage,
  type AuthResponse,
  type UpdateProfilePayload,
  type ChangePasswordPayload,
  type ChangePasswordResponse,
} from '@/api/authAPI'
import { endSession, getAccessToken, getSessionGeneration, startSession } from '@/api/session'
import { sessionEnded, tokenRefreshed } from '../sessionActions'

// anonymous       — токена нет;
// restoring       — токен есть, профиль ещё не подтверждён сервером (наличие токена ≠ вход);
// authenticated   — профиль загружен;
// reauth_required — сессия отозвана или истекла, нужен повторный вход;
// restore_failed  — не удалось связаться с сервером при восстановлении: токен оставлен, можно повторить.
export type SessionStatus = 'anonymous' | 'restoring' | 'authenticated' | 'reauth_required' | 'restore_failed'
export type SessionNotice = 'session_expired' | 'password_changed' | null

export interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  sessionStatus: SessionStatus
  sessionNotice: SessionNotice
  loading: boolean
  error: string | null
}

interface RejectedActionLike {
  payload?: unknown
  error?: {
    message?: string
  }
}

interface LoadUserRejection {
  message: string
  // 401/403 после неудачного refresh: токен недействителен. Иначе ошибка временная.
  unauthorized: boolean
}

type SessionThunkConfig<Rejected = string> = {
  rejectValue: Rejected
  fulfilledMeta: { sessionGeneration: number }
  rejectedMeta: { sessionGeneration: number }
}

// Начальное состояние читает localStorage в момент вызова: после сброса на выходе токена уже нет.
export const createAuthInitialState = (): AuthState => {
  const token = getAccessToken()
  return {
    user: null,
    token,
    isAuthenticated: false,
    sessionStatus: token ? 'restoring' : 'anonymous',
    sessionNotice: null,
    loading: false,
    error: null,
  }
}

export const login = createAsyncThunk<AuthResponse, LoginCredentials, SessionThunkConfig>(
  'auth/login',
  async (credentials, { rejectWithValue, fulfillWithValue }) => {
    const generation = getSessionGeneration()
    try {
      const response = await loginAPI(credentials)
      if (generation !== getSessionGeneration()) return rejectWithValue('Session changed', { sessionGeneration: generation })
      startSession({ token: response.token, refreshToken: response.refreshToken })
      return fulfillWithValue(response, { sessionGeneration: getSessionGeneration() })
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to sign in.'), { sessionGeneration: generation })
    }
  },
)

export const register = createAsyncThunk<AuthResponse, RegisterData, SessionThunkConfig>(
  'auth/register',
  async (data, { rejectWithValue, fulfillWithValue }) => {
    const generation = getSessionGeneration()
    try {
      const response = await registerAPI(data)
      if (generation !== getSessionGeneration()) return rejectWithValue('Session changed', { sessionGeneration: generation })
      startSession({ token: response.token, refreshToken: response.refreshToken })
      return fulfillWithValue(response, { sessionGeneration: getSessionGeneration() })
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to create the account.'), { sessionGeneration: generation })
    }
  },
)

export const loadUser = createAsyncThunk<User, void, SessionThunkConfig<LoadUserRejection>>(
  'auth/loadUser',
  async (_, { rejectWithValue, fulfillWithValue }) => {
    const generation = getSessionGeneration()
    try {
      const user = await getCurrentUser()
      return fulfillWithValue(user, { sessionGeneration: generation })
    } catch (error) {
      if (generation !== getSessionGeneration()) {
        return rejectWithValue({ message: 'Session changed', unauthorized: false }, { sessionGeneration: generation })
      }
      const status = axios.isAxiosError(error) ? error.response?.status : undefined
      const unauthorized = status === 401 || status === 403
      if (unauthorized && getAccessToken()) {
        // HTTP-слой мог уже завершить сессию (refresh не удался); если нет — завершаем здесь.
        endSession('reauth_required')
      }
      return rejectWithValue({
        message: getApiErrorMessage(error, 'Unable to load the current user.'),
        unauthorized,
      }, { sessionGeneration: getSessionGeneration() })
    }
  },
)

// Выход: сервер отзывает сессию по возможности (best-effort), локальное состояние очищается всегда.
export const logout = createAsyncThunk<void, void, SessionThunkConfig>('auth/logout', async (_, { rejectWithValue, fulfillWithValue }) => {
  const generation = getSessionGeneration()
  try {
    if (getAccessToken()) {
      await logoutAPI()
    }
  } catch {
    // Токен мог уже истечь или сервер быть недоступен: локальный выход это не отменяет.
  }
  if (generation !== getSessionGeneration()) return rejectWithValue('Session changed', { sessionGeneration: generation })
  endSession('logout')
  return fulfillWithValue(undefined, { sessionGeneration: getSessionGeneration() })
})

export const updateProfile = createAsyncThunk<User, UpdateProfilePayload, SessionThunkConfig>(
  'auth/updateProfile',
  async (data, { rejectWithValue, fulfillWithValue }) => {
    const generation = getSessionGeneration()
    try {
      return fulfillWithValue(await updateProfileAPI(data), { sessionGeneration: generation })
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to update the profile.'), { sessionGeneration: generation })
    }
  },
)

export const changePassword = createAsyncThunk<ChangePasswordResponse, ChangePasswordPayload, SessionThunkConfig>(
  'auth/changePassword',
  async (data, { rejectWithValue, fulfillWithValue }) => {
    const generation = getSessionGeneration()
    try {
      const response = await changePasswordAPI(data)
      if (generation !== getSessionGeneration()) return rejectWithValue('Session changed', { sessionGeneration: generation })
      if (response.reauth_required) {
        // Все выпущенные токены отозваны сервером: продолжать с ними бессмысленно.
        endSession('password_changed')
      }
      return fulfillWithValue(response, { sessionGeneration: getSessionGeneration() })
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to change the password.'), { sessionGeneration: generation })
    }
  },
)

const getErrorPayload = (action: RejectedActionLike, fallback: string) => {
  if (typeof action.payload === 'string' && action.payload.trim()) {
    return action.payload
  }
  if (typeof action.error?.message === 'string' && action.error.message.trim()) {
    return action.error.message
  }
  return fallback
}

const authSlice = createSlice({
  name: 'auth',
  initialState: createAuthInitialState,
  reducers: {
    clearError: (state) => {
      state.error = null
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(sessionEnded, (_state, action) => {
        // Токены уже стёрты: начальное состояние — «аноним», плюс причина для страницы входа.
        const next = createAuthInitialState()
        const { reason } = action.payload
        if (reason === 'reauth_required') {
          next.sessionStatus = 'reauth_required'
          next.sessionNotice = 'session_expired'
        } else if (reason === 'password_changed') {
          next.sessionStatus = 'reauth_required'
          next.sessionNotice = 'password_changed'
        }
        return next
      })
      .addCase(tokenRefreshed, (state, action) => {
        if (state.sessionStatus === 'authenticated' || state.sessionStatus === 'restoring') {
          state.token = action.payload.token
        }
      })
      .addCase(login.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(login.fulfilled, (state, action) => {
        state.loading = false
        state.user = action.payload.user
        state.token = action.payload.token
        state.isAuthenticated = true
        state.sessionStatus = 'authenticated'
        state.sessionNotice = null
        state.error = null
      })
      .addCase(login.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Unable to sign in.')
      })
      .addCase(register.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(register.fulfilled, (state, action) => {
        state.loading = false
        state.user = action.payload.user
        state.token = action.payload.token
        state.isAuthenticated = true
        state.sessionStatus = 'authenticated'
        state.sessionNotice = null
        state.error = null
      })
      .addCase(register.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Unable to create the account.')
      })
      .addCase(loadUser.pending, (state) => {
        state.error = null
        if (state.sessionStatus === 'restore_failed') {
          state.sessionStatus = 'restoring'
        }
      })
      .addCase(loadUser.fulfilled, (state, action) => {
        // Ответ приходит позже выхода или повторного входа — не воскрешаем старую сессию.
        if (state.sessionStatus !== 'restoring' && state.sessionStatus !== 'restore_failed') {
          return
        }
        state.user = action.payload
        state.isAuthenticated = true
        state.sessionStatus = 'authenticated'
      })
      .addCase(loadUser.rejected, (state, action) => {
        if (state.sessionStatus !== 'restoring' && state.sessionStatus !== 'restore_failed') {
          return
        }
        const rejection = action.payload
        if (rejection?.unauthorized) {
          // Токен недействителен (refresh не помог): сессия завершена. Остальные срезы очистит
          // корневой редьюсер по sessionEnded, которое придёт от HTTP-слоя; здесь страхуем только auth.
          state.user = null
          state.token = null
          state.isAuthenticated = false
          state.sessionStatus = 'reauth_required'
          state.sessionNotice = 'session_expired'
          return
        }
        state.sessionStatus = 'restore_failed'
        state.error = rejection?.message ?? action.error.message ?? 'Unable to load the current user.'
      })
      .addCase(updateProfile.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(updateProfile.fulfilled, (state, action) => {
        state.loading = false
        state.user = action.payload
        state.error = null
      })
      .addCase(updateProfile.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Unable to update the profile.')
      })
      .addCase(changePassword.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(changePassword.fulfilled, (state) => {
        state.loading = false
        state.error = null
      })
      .addCase(changePassword.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Unable to change the password.')
      })
  },
})

export const { clearError } = authSlice.actions
export default authSlice.reducer
