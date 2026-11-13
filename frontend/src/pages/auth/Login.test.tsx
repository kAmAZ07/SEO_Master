import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import Login from './Login'
import { renderWithStore } from '../../test/renderWithStore'

const renderLogin = (sessionNotice: 'session_expired' | 'password_changed' | null) =>
  renderWithStore(
    <Routes>
      <Route path="/login" element={<Login />} />
    </Routes>,
    { route: '/login', preloaded: { auth: { sessionNotice, sessionStatus: sessionNotice ? 'reauth_required' : 'anonymous' } } },
  )

describe('Login: причина повторного входа', () => {
  it('истёкшая сессия', () => {
    renderLogin('session_expired')
    expect(screen.getByRole('status')).toHaveTextContent('Сессия истекла или была завершена')
  })

  it('смена пароля', () => {
    renderLogin('password_changed')
    expect(screen.getByRole('status')).toHaveTextContent('Пароль изменён, все сессии завершены')
  })

  it('без причины сообщения нет', () => {
    renderLogin(null)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
