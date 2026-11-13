import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Audit from './Audit'
import { authenticatedState, renderWithStore } from '../test/renderWithStore'
import { PENDING_PROJECT_SAVE_KEY } from '../api/session'
import type { AuditStatus, AuditWorkflowStatus } from '../types/audit'

const publicApi = vi.hoisted(() => ({ getAuditStatus: vi.fn(), submitAuditRequest: vi.fn(), cancelAudit: vi.fn() }))
vi.mock('../api/publicAuditAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/publicAuditAPI')>()),
  ...publicApi,
}))

const dashboardApi = vi.hoisted(() => ({ createProject: vi.fn() }))
vi.mock('../api/dashboardAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/dashboardAPI')>()),
  ...dashboardApi,
}))

const audit = (status: AuditWorkflowStatus, over: Partial<AuditStatus> = {}): AuditStatus => ({
  uid: 'a1', id: 'a1', url: 'https://example.com/', status, progress: 50, createdAt: '2026-11-10T00:00:00Z',
  score: 62, issues: { passed: 1, warnings: 1, errors: 1 },
  details: [{ code: 'missing_title', title: 'Нет title', description: 'd', severity: 'high', status: 'error' }],
  pages: [{ url: 'https://example.com/' }], ...over,
})

const renderAudit = (route: string, authenticated = true) =>
  renderWithStore(
    <Routes>
      <Route path="/audit/results/:uid" element={<Audit />} />
      <Route path="/audit" element={<Audit />} />
      <Route path="/dashboard/projects/:id/audits/:uid" element={<p>Отчёт проекта</p>} />
      <Route path="/dashboard/projects/:id" element={<p>Карточка проекта</p>} />
      <Route path="/register" element={<p>Регистрация</p>} />
    </Routes>,
    { route, preloaded: authenticated ? authenticatedState() : {} },
  )

describe('Audit: автосохранение проекта', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    publicApi.getAuditStatus.mockResolvedValue(audit('completed'))
  })

  it('неудачное автосохранение выполняется один раз, без бесконечного цикла, и предлагает «Повторить»', async () => {
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'a1', url: 'https://example.com/' }))
    dashboardApi.createProject.mockRejectedValue(new Error('Сервер недоступен'))

    renderAudit('/audit/results/a1?saveProject=1')

    expect(await screen.findByRole('button', { name: 'Повторить сохранение' })).toBeInTheDocument()
    // Даём эффектам шанс перезапуститься: цикл вызвал бы create снова и снова.
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(dashboardApi.createProject).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toHaveTextContent(/Сервер недоступен|Failed to create the project|Не удалось сохранить проект/)
  })

  it('«Повторить» делает ровно один новый запрос и при успехе ведёт в проект', async () => {
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'a1', url: 'https://example.com/' }))
    dashboardApi.createProject.mockRejectedValueOnce(new Error('временная ошибка'))
    dashboardApi.createProject.mockResolvedValueOnce({ id: 'p1', name: 'example.com', url: 'https://example.com/', status: 'active' })

    renderAudit('/audit/results/a1?saveProject=1')
    await userEvent.click(await screen.findByRole('button', { name: 'Повторить сохранение' }))

    expect(await screen.findByText('Отчёт проекта')).toBeInTheDocument()
    expect(dashboardApi.createProject).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem(PENDING_PROJECT_SAVE_KEY)).toBeNull()
    // Оба запроса несут один и тот же аудит: на сервере создание идемпотентно.
    expect(dashboardApi.createProject.mock.calls[1][0]).toEqual(dashboardApi.createProject.mock.calls[0][0])
  })

  it('успешное автосохранение выполняется один раз', async () => {
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'a1', url: 'https://example.com/' }))
    dashboardApi.createProject.mockResolvedValue({ id: 'p1', name: 'example.com', url: 'https://example.com/', status: 'active' })

    renderAudit('/audit/results/a1?saveProject=1')

    expect(await screen.findByText('Отчёт проекта')).toBeInTheDocument()
    expect(dashboardApi.createProject).toHaveBeenCalledTimes(1)
    expect(dashboardApi.createProject).toHaveBeenCalledWith({ name: 'example.com', url: 'https://example.com/', sourceAuditId: 'a1' })
  })

  it('отклонённое присоединение аудита ведёт на карточку проекта, а не в отчёт', async () => {
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'a1', url: 'https://example.com/' }))
    dashboardApi.createProject.mockResolvedValue({
      id: 'p1', name: 'example.com', url: 'https://example.com/', status: 'active', claim: { status: 'rejected', reason: 'already_claimed' },
    })

    renderAudit('/audit/results/a1?saveProject=1')

    expect(await screen.findByText('Карточка проекта')).toBeInTheDocument()
  })

  it('без ?saveProject=1 и без входа сохранение само не запускается', async () => {
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid: 'a1', url: 'https://example.com/' }))
    renderAudit('/audit/results/a1')
    await screen.findByText('Вердикт по сайту')
    expect(dashboardApi.createProject).not.toHaveBeenCalled()
  })

  it('гость по кнопке «Сохранить проект» запоминает намерение и уходит на регистрацию', async () => {
    renderAudit('/audit/results/a1', false)
    await userEvent.click(await screen.findByRole('button', { name: 'Сохранить проект в кабинете' }))

    expect(await screen.findByText('Регистрация')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(PENDING_PROJECT_SAVE_KEY) ?? 'null')).toMatchObject({ uid: 'a1' })
    expect(dashboardApi.createProject).not.toHaveBeenCalled()
  })
})

describe('Audit: состояния и оценка', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('оценка подписана как эвристика', async () => {
    publicApi.getAuditStatus.mockResolvedValue(audit('completed'))
    renderAudit('/audit/results/a1', false)
    expect(await screen.findByText('Итоговая оценка (эвристика)')).toBeInTheDocument()
  })

  it('без оценки от сервера показывается «Оценка недоступна», а не число', async () => {
    publicApi.getAuditStatus.mockResolvedValue(audit('completed', { score: null }))
    renderAudit('/audit/results/a1', false)
    expect(await screen.findByText('Оценка недоступна')).toBeInTheDocument()
  })

  it('partial: баннер и результаты вместе', async () => {
    publicApi.getAuditStatus.mockResolvedValue(audit('partial', { partialReasons: ['deadline'] }))
    renderAudit('/audit/results/a1', false)
    expect(await screen.findByText('Аудит завершён частично')).toBeInTheDocument()
    expect(screen.getByText('Вердикт по сайту')).toBeInTheDocument()
  })

  it.each([
    ['cancelled', 'Аудит отменён'],
    ['expired', 'Срок хранения результата истёк'],
    ['failed', 'Аудит завершился с ошибкой'],
  ] as const)('%s не показывает результаты', async (status, heading) => {
    publicApi.getAuditStatus.mockResolvedValue(audit(status))
    renderAudit('/audit/results/a1', false)
    expect(await screen.findByText(heading)).toBeInTheDocument()
    expect(screen.queryByText('Вердикт по сайту')).not.toBeInTheDocument()
  })

  it('отмена активного публичного аудита вызывает API', async () => {
    publicApi.getAuditStatus.mockResolvedValue(audit('running'))
    publicApi.cancelAudit.mockResolvedValue(audit('cancelled'))
    renderAudit('/audit/results/a1', false)

    await userEvent.click(await screen.findByRole('button', { name: 'Отменить аудит' }))

    await waitFor(() => expect(publicApi.cancelAudit).toHaveBeenCalledWith('a1'))
    expect(await screen.findByText('Аудит отменён')).toBeInTheDocument()
  })

  it('истёкший отчёт (404/410) показывается как недоступный, без бесконечной загрузки', async () => {
    publicApi.getAuditStatus.mockRejectedValue(Object.assign(new Error('gone'), { isAxiosError: true, response: { status: 410 } }))
    renderAudit('/audit/results/a1', false)
    expect(await screen.findByRole('heading', { name: 'Срок хранения отчёта истёк' })).toBeInTheDocument()
    expect(publicApi.getAuditStatus).toHaveBeenCalledTimes(1)
  })

  it('прежний результат не показывается на новом маршруте без uid', async () => {
    publicApi.getAuditStatus.mockResolvedValue(audit('completed'))
    const { store } = renderAudit('/audit/results/a1', false)
    await screen.findByText('Вердикт по сайту')
    expect(store.getState().audit.currentAudit?.uid).toBe('a1')
  })

  it('на /audit без uid чужой результат из стора не показывается', async () => {
    renderWithStore(<Audit />, {
      route: '/audit',
      preloaded: { audit: { currentAudit: audit('completed'), activeUid: 'a1' } },
    })
    await waitFor(() => expect(screen.queryByText('Вердикт по сайту')).not.toBeInTheDocument())
  })
})
