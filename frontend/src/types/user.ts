export interface User {
  id: string;
  email: string;
  name: string;
  role: 'admin' | 'user';
  company?: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterData {
  email: string;
  password: string;
  name: string;
}

export interface UpdateProfilePayload {
  name: string;
  email: string;
  company?: string;
}

export interface ChangePasswordPayload {
  currentPassword: string;
  newPassword: string;
}
