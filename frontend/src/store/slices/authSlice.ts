import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import {
  ChangePasswordPayload,
  LoginCredentials,
  RegisterData,
  UpdateProfilePayload,
  User,
} from '@/types/user'
import {
  changePassword as changePasswordAPI,
  getCurrentUser,
  login as loginAPI,
  register as registerAPI,
  updateProfile as updateProfileAPI,
} from '@/api/authAPI'


interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  loading: boolean
  error: string | null
}


const initialToken = localStorage.getItem('token')

const initialState: AuthState = {
  user: null,
  token: initialToken,
  isAuthenticated: Boolean(initialToken),
  loading: false,
  error: null,
}


export const login = createAsyncThunk(
  'auth/login',
  async (credentials: LoginCredentials) => {
    return await loginAPI(credentials)
  }
)


export const register = createAsyncThunk(
  'auth/register',
  async (data: RegisterData) => {
    return await registerAPI(data)
  }
)


export const loadUser = createAsyncThunk('auth/loadUser', async () => {
  return await getCurrentUser()
})


export const updateProfile = createAsyncThunk(
  'auth/updateProfile',
  async (payload: UpdateProfilePayload) => {
    return await updateProfileAPI(payload)
  }
)


export const changePassword = createAsyncThunk(
  'auth/changePassword',
  async (payload: ChangePasswordPayload) => {
    await changePasswordAPI(payload)
  }
)


const authSlice = createSlice({
  name: 'auth',
  initialState,
  reducers: {
    logout: (state) => {
      state.user = null
      state.token = null
      state.isAuthenticated = false
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
        localStorage.setItem('token', action.payload.token)
      })
      .addCase(login.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Login failed'
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
        localStorage.setItem('token', action.payload.token)
      })
      .addCase(register.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Registration failed'
      })

      .addCase(loadUser.pending, (state) => {
        state.loading = true
      })
      .addCase(loadUser.fulfilled, (state, action) => {
        state.loading = false
        state.user = action.payload
        state.isAuthenticated = true
      })
      .addCase(loadUser.rejected, (state) => {
        state.loading = false
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
      })
      .addCase(updateProfile.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to update profile'
      })

      .addCase(changePassword.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(changePassword.fulfilled, (state) => {
        state.loading = false
      })
      .addCase(changePassword.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to change password'
      })
  },
})


export const { logout, clearError } = authSlice.actions
export default authSlice.reducer
