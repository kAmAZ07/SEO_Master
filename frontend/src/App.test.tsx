import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'

const authApi = vi.hoisted(() => ({ getCurrentUser: vi.fn() }))
vi.mock('@/api/authAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/authAPI')>()),
  getCurrentUser: authApi.getCurrentUser,
}))

// Маршрутизатор создаётся при загрузке модуля по текущему адресу: для каждого сценария модуль грузится заново.
const renderAppAt = async (path: string) => {
  window.history.pushState({}, '', path)
  vi.resetModules()
  const { default: App } = await import('./App')
  return render(<App />)
}

describe('App: маршруты и восстановление сессии', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })
  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('несуществующий адрес открывает страницу 404', async () => {
    await renderAppAt('/definitely/missing')
    expect(await screen.findByText('Страница не найдена')).toBeInTheDocument()
  })

  it('несуществующий раздел кабинета тоже ведёт на 404', async () => {
    await renderAppAt('/dashboard/no-such-section')
    expect(await screen.findByText('Страница не найдена')).toBeInTheDocument()
  })

  it('устаревший токен: профиль запрашивается, закрытая страница не открывается, на входе объяснена причина', async () => {
    localStorage.setItem('token', 'stale-token')
    authApi.getCurrentUser.mockRejectedValue(
      new AxiosError('x', 'ERR', undefined, null, {
        status: 401, statusText: '', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
      }),
    )

    await renderAppAt('/dashboard/settings')

    expect(await screen.findByText('Сессия истекла или была завершена. Войдите снова.')).toBeInTheDocument()
    expect(authApi.getCurrentUser).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('token')).toBeNull()
    expect(window.location.pathname).toBe('/login')
    expect(window.location.search).toBe(`?redirect=${encodeURIComponent('/dashboard/settings')}`)
  })
})
