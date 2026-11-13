import type { ReactElement } from 'react'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

import { createAppStore } from '../store/store'
import type { RootState } from '../store/store'

type Preloaded = Partial<{ [K in keyof RootState]: Partial<RootState[K]> }>

export const authenticatedState = (): Preloaded => ({
  auth: {
    user: { id: 'u1', email: 'u1@example.com', name: 'u1', role: 'user' },
    token: 'token',
    isAuthenticated: true,
    sessionStatus: 'authenticated',
    sessionNotice: null,
    loading: false,
    error: null,
  },
})

// Рендерит компонент с настоящим стором (с редьюсерами и корневым сбросом) и роутером.
export const renderWithStore = (ui: ReactElement, options: { route?: string | { pathname: string; state: unknown }; preloaded?: Preloaded } = {}) => {
  const base = createAppStore().getState()
  const preloaded = Object.fromEntries(
    Object.entries(base).map(([key, value]) => [key, { ...value, ...(options.preloaded?.[key as keyof RootState] ?? {}) }]),
  ) as RootState
  const store = createAppStore(preloaded)
  const result = render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[options.route ?? '/']}>{ui}</MemoryRouter>
    </Provider>,
  )
  return { store, ...result }
}
