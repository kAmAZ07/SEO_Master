import api from './axiosConfig'
import {
  ChangePasswordPayload,
  LoginCredentials,
  RegisterData,
  UpdateProfilePayload,
  User,
} from '@/types/user'

interface AuthResponse {
  user: User
  token: string
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

export const updateProfile = async (payload: UpdateProfilePayload): Promise<User> => {
  const response = await api.patch('/auth/profile', payload)
  return response.data
}

export const changePassword = async (payload: ChangePasswordPayload): Promise<void> => {
  await api.post('/auth/change-password', payload)
}

export const logout = async (): Promise<void> => {
  await api.post('/auth/logout')
}
