import axios from 'axios'
import api from './axiosConfig'
import { User, LoginCredentials, RegisterData } from '@/types/user'

export interface AuthResponse {
  success?: boolean
  user: User
  token: string
  refreshToken?: string
}

export interface UpdateProfilePayload {
  name?: string
  email?: string
  company?: string
}

export interface ChangePasswordPayload {
  currentPassword: string
  newPassword: string
}

export const getApiErrorMessage = (error: unknown, fallback: string): string => {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data
    const detail = data?.detail
    const wrappedError = data?.error

    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }

    if (wrappedError && typeof wrappedError === 'object') {
      if (typeof wrappedError.message === 'string' && wrappedError.message.trim()) {
        return wrappedError.message
      }
      if (typeof wrappedError.error === 'string' && wrappedError.error.trim()) {
        return wrappedError.error
      }
      if (Array.isArray(wrappedError.details) && wrappedError.details.length > 0) {
        const firstItem = wrappedError.details[0]
        if (typeof firstItem?.message === 'string' && firstItem.message.trim()) {
          return firstItem.message
        }
        if (typeof firstItem?.msg === 'string' && firstItem.msg.trim()) {
          return firstItem.msg
        }
      }
    }

    if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string' && detail.message.trim()) {
        return detail.message
      }
      if (typeof detail.error === 'string' && detail.error.trim()) {
        return detail.error
      }
    }

    if (Array.isArray(detail) && detail.length > 0) {
      const firstItem = detail[0]
      if (typeof firstItem?.msg === 'string' && firstItem.msg.trim()) {
        return firstItem.msg
      }
    }

    if (typeof error.message === 'string' && error.message.trim() && error.message !== 'Network Error') {
      return error.message
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message
  }

  return fallback
}

export const login = async (credentials: LoginCredentials): Promise<AuthResponse> => {
  const response = await api.post('/auth/login', credentials)
  return response.data
}

export const register = async (data: RegisterData): Promise<AuthResponse> => {
  const response = await api.post('/auth/register', data)
  return response.data
}

export const getCurrentUser = async (): Promise<User> => {
  const response = await api.get('/auth/me')
  return response.data
}

export const updateProfile = async (data: UpdateProfilePayload): Promise<User> => {
  const response = await api.patch('/auth/profile', data)
  return response.data
}

export const changePassword = async (data: ChangePasswordPayload): Promise<{ success: boolean }> => {
  const response = await api.post('/auth/change-password', data)
  return response.data
}

export const logout = async (): Promise<void> => {
  await api.post('/auth/logout')
}
