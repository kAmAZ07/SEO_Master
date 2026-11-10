import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectDetail from './ProjectDetail'
import { authenticatedState, renderWithStore } from '../test/renderWithStore'
import type { Project } from '../types/dashboard'

const dashboardApi = vi.hoisted(() => ({
  fetchProjectDetails: vi.fn(),
  fetchVerification: vi.fn(),
  requestVerificationChallenge: vi.fn(),
  checkVerification: vi.fn(),
  revokeVerification: vi.fn(),
}))
vi.mock('../api/dashboardAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/dashboardAPI')>()),
  ...dashboardApi,
}))
vi.mock('../components/projects/ProjectAuditTab', () => ({ default: () => <p>Вкладка аудита</p> }))
vi.mock('../components/projects/ProjectIntegrationsTab', () => ({ default: () => <p>Вкладка интеграций</p> }))

const project = (over: Partial<Project> = {}): Project => ({
  id: 'p1', name: 'Мой сайт', url: 'https://example.com', status: 'active',
  scope: { origin: 'https://example.com', pathPrefix: '/' },
  verification: { state: 'unverified', scope: { origin: 'https://example.com', pathPrefix: '/' } },
  ...over,
})

const httpError = (status: number) =>
  new AxiosError('x', 'ERR', undefined, null, {
    status, statusText: '', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
  })

const renderDetail = (route: string | { pathname: string; state: unknown } = '/dashboard/projects/p1', preloaded = authenticatedState()) =>
  renderWithStore(
    <Routes>
      <Route path="/dashboard/projects/:id" element={<ProjectDetail />} />
    </Routes>,
    { route, preloaded },
  )

describe('ProjectDetail: состояния проекта и владения', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('показывает проект, подтверждение владения и FF-Score', async () => {
    dashboardApi.fetchProjectDetails.mockResolvedValue(
      project({ ffScore: { total: null, status: 'insufficient_data', formulaVersion: 'ff-2', heuristic: true, legacyEstimated: false, freshness: null, familiarity: null, quality: null, timestamp: 't' } }),
    )
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Мой сайт' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Подтверждение владения сайтом' })).toBeInTheDocument()
    expect(screen.getByText('Не подтверждён')).toBeInTheDocument()
    expect(screen.getByText('Полный балл недоступен: недостаточно данных')).toBeInTheDocument()
  })

  it('404: «Проект не найден» без кнопки повтора', async () => {
    dashboardApi.fetchProjectDetails.mockRejectedValue(httpError(404))
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Проект не найден' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
  })

  it('ошибка сервера отличается от 404 и даёт «Повторить»', async () => {
    dashboardApi.fetchProjectDetails.mockRejectedValueOnce(httpError(503)).mockResolvedValueOnce(project())
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Не удалось загрузить проект' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByRole('heading', { name: 'Мой сайт' })).toBeInTheDocument()
  })

  it('проект из прежнего маршрута не показывается, пока грузится новый', async () => {
    let resolveProject: (value: Project) => void = () => undefined
    dashboardApi.fetchProjectDetails.mockReturnValue(new Promise<Project>((resolve) => { resolveProject = resolve }))
    const preloaded = { ...authenticatedState(), dashboard: { currentProject: project({ id: 'other', name: 'Чужой проект' }) } }
    renderDetail('/dashboard/projects/p1', preloaded)

    expect(screen.queryByText('Чужой проект')).not.toBeInTheDocument()
    resolveProject(project())
    expect(await screen.findByRole('heading', { name: 'Мой сайт' })).toBeInTheDocument()
    expect(screen.queryByText('Чужой проект')).not.toBeInTheDocument()
  })

  it('scope_review: предупреждение, нет действий по подтверждению', async () => {
    dashboardApi.fetchProjectDetails.mockResolvedValue(project({ status: 'scope_review', scope: null, verification: null }))
    renderDetail()

    expect(await screen.findByText(/не удалось определить область сайта/)).toBeInTheDocument()
    // Статус проекта в деталях и бейдж в блоке подтверждения.
    expect(screen.getAllByText('Требует проверки области')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Получить файл подтверждения' })).not.toBeInTheDocument()
  })

  it('отклонённое присоединение аудита объясняется на карточке проекта', async () => {
    dashboardApi.fetchProjectDetails.mockResolvedValue(project())
    renderDetail({ pathname: '/dashboard/projects/p1', state: { claimRejected: 'already_claimed' } })

    expect(await screen.findByText(/уже присоединён к другому проекту/)).toBeInTheDocument()
  })

  it('смена проекта в маршруте загружает новый проект', async () => {
    dashboardApi.fetchProjectDetails.mockImplementation(async (id: string) => project({ id, name: `Проект ${id}` }))
    renderDetail()
    await screen.findByRole('heading', { name: 'Проект p1' })
    await waitFor(() => expect(dashboardApi.fetchProjectDetails).toHaveBeenCalledWith('p1'))
  })
})
