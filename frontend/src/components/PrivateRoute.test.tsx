import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PrivateRoute from './PrivateRoute'
import { renderWithStore } from '../test/renderWithStore'
import type { SessionStatus } from '../store/slices/authSlice'

const authApi = vi.hoisted(() => ({ getCurrentUser: vi.fn() }))
vi.mock('@/api/authAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/authAPI')>()),
  getCurrentUser: authApi.getCurrentUser,
}))

const LoginProbe = () => {
  const location = useLocation()
  return <p>Вход: {location.pathname}{location.search}</p>
}

const renderRoute = (sessionStatus: SessionStatus, error: string | null = null) =>
  renderWithStore(
    <Routes>
      <Route path="/login" element={<LoginProbe />} />
      <Route path="/dashboard/*" element={<PrivateRoute><p>Закрытая страница</p></PrivateRoute>} />
    </Routes>,
    { route: '/dashboard/projects?tab=audit', preloaded: { auth: { sessionStatus, error, isAuthenticated: sessionStatus === 'authenticated' } } },
  )

describe('PrivateRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('пока профиль не подтверждён сервером, закрытая страница не показывается', () => {
    renderRoute('restoring')
    expect(screen.queryByText('Закрытая страница')).not.toBeInTheDocument()
    expect(screen.getByText('Загрузка...')).toBeInTheDocument()
  })

  it('вошедшему пользователю показывает страницу', () => {
    renderRoute('authenticated')
    expect(screen.getByText('Закрытая страница')).toBeInTheDocument()
  })

  it.each<SessionStatus>(['anonymous', 'reauth_required'])('%s ведёт на вход с возвратом на текущую страницу', (status) => {
    renderRoute(status)
    expect(screen.getByText(`Вход: /login?redirect=${encodeURIComponent('/dashboard/projects?tab=audit')}`)).toBeInTheDocument()
  })

  it('недоступность сервера при восстановлении: сообщение и «Повторить», токен не теряется', async () => {
    authApi.getCurrentUser.mockResolvedValue({ id: 'u1', email: 'u1@example.com', name: 'u1', role: 'user' })
    const { store } = renderRoute('restore_failed', 'Сервер недоступен')
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось восстановить сессию')

    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('Закрытая страница')).toBeInTheDocument()
    expect(store.getState().auth.sessionStatus).toBe('authenticated')
  })
})
