import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { User, LoginCredentials, RegisterData } from '@/types/user'
import {
  login as loginAPI,
  register as registerAPI,
  getCurrentUser,
  updateProfile as updateProfileAPI,
  changePassword as changePasswordAPI,
  getApiErrorMessage,
} from '@/api/authAPI'

interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  loading: boolean
  error: string | null
}

interface RejectedActionLike {
  payload?: unknown
  error?: {
    message?: string
  }
}

const initialState: AuthState = {
  user: null,
  token: localStorage.getItem('token'),
  isAuthenticated: Boolean(localStorage.getItem('token')),
  loading: false,
  error: null,
}

export const login = createAsyncThunk(
  'auth/login',
  async (credentials: LoginCredentials, { rejectWithValue }) => {
    try {
      return await loginAPI(credentials)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to sign in.'))
    }
  },
)

export const register = createAsyncThunk(
  'auth/register',
  async (data: RegisterData, { rejectWithValue }) => {
    try {
      return await registerAPI(data)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to create the account.'))
    }
  },
)

export const loadUser = createAsyncThunk(
  'auth/loadUser',
  async (_, { rejectWithValue }) => {
    try {
      return await getCurrentUser()
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to load the current user.'))
    }
  },
)

export const updateProfile = createAsyncThunk(
  'auth/updateProfile',
  async (data: { name?: string; email?: string; company?: string }, { rejectWithValue }) => {
    try {
      return await updateProfileAPI(data)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to update the profile.'))
    }
  },
)

export const changePassword = createAsyncThunk(
  'auth/changePassword',
  async (data: { currentPassword: string; newPassword: string }, { rejectWithValue }) => {
    try {
      return await changePasswordAPI(data)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Unable to change the password.'))
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
  initialState,
  reducers: {
    logout: (state) => {
      state.user = null
      state.token = null
      state.isAuthenticated = false
      state.error = null
      localStorage.removeItem('token')
    },
    clearError: (state) => {
      state.error = null
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(login.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(login.fulfilled, (state, action) => {
        state.loading = false
        state.user = action.payload.user
        state.token = action.payload.token
        state.isAuthenticated = true
        state.error = null
        localStorage.setItem('token', action.payload.token)
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
        state.error = null
        localStorage.setItem('token', action.payload.token)
      })
      .addCase(register.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Unable to create the account.')
      })
      .addCase(loadUser.fulfilled, (state, action) => {
        state.user = action.payload
        state.isAuthenticated = true
      })
      .addCase(loadUser.rejected, (state) => {
        state.user = null
        state.token = null
        state.isAuthenticated = false
        localStorage.removeItem('token')
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

export const { logout, clearError } = authSlice.actions
export default authSlice.reducer
