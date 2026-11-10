import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectOwnership from './ProjectOwnership'
import type { VerificationSummary } from '../../types/dashboard'

const api = vi.hoisted(() => ({
  fetchVerification: vi.fn(),
  requestVerificationChallenge: vi.fn(),
  checkVerification: vi.fn(),
  revokeVerification: vi.fn(),
}))
vi.mock('../../api/dashboardAPI', () => api)

const scope = { origin: 'https://example.com', pathPrefix: '/blog' }
const summary = (over: Partial<VerificationSummary> = {}): VerificationSummary => ({ state: 'unverified', scope, ...over })

const httpError = (status: number, data: unknown = {}, headers: Record<string, string> = {}) =>
  new AxiosError('x', 'ERR', undefined, null, {
    status, statusText: '', headers, data, config: {} as InternalAxiosRequestConfig,
  })

const renderOwnership = (value: VerificationSummary | null = summary(), scopeReview = false) =>
  render(<ProjectOwnership projectId="p1" summary={value} scope={scope} scopeReview={scopeReview} />)

describe('ProjectOwnership', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it.each([
    [summary(), 'Не подтверждён'],
    [summary({ state: 'pending' }), 'Ожидает проверки файла'],
    [summary({ state: 'verified', freshUntil: '2026-12-10T00:00:00Z' }), 'Подтверждён, действует до'],
    [summary({ state: 'stale', verifiedAt: '2026-09-01T00:00:00Z' }), 'Подтверждение от'],
  ])('показывает состояние подтверждения', (value, expected) => {
    renderOwnership(value)
    expect(screen.getByText(new RegExp(expected))).toBeInTheDocument()
    expect(screen.getByText('https://example.com/blog')).toBeInTheDocument()
  })

  it('проект без области: подтверждать нельзя, кнопок действий нет', () => {
    renderOwnership(null, true)
    expect(screen.getAllByText(/Требует проверки области/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Получить файл подтверждения' })).not.toBeInTheDocument()
  })

  it('выдача файла показывает адрес и содержимое один раз, фокус переходит на инструкцию', async () => {
    api.requestVerificationChallenge.mockResolvedValue({
      fileUrl: 'https://example.com/blog/seo-master-challenge.txt',
      fileBody: 'seo-master-site-verification=abc',
      expiresAt: '2026-11-11T00:00:00Z',
      verification: summary({ state: 'pending' }),
    })
    renderOwnership()

    await userEvent.click(screen.getByRole('button', { name: 'Получить файл подтверждения' }))

    expect(await screen.findByText('https://example.com/blog/seo-master-challenge.txt')).toBeInTheDocument()
    expect(screen.getByText('seo-master-site-verification=abc')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Как подтвердить' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Скопировать адрес файла' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Проверить' })).toBeEnabled()
  })

  it('«Проверить» недоступна, пока нет выданного файла', () => {
    renderOwnership(summary())
    expect(screen.getByRole('button', { name: 'Проверить' })).toBeDisabled()
  })

  it('успешная проверка переводит в «Подтверждён»', async () => {
    api.checkVerification.mockResolvedValue({
      verified: true, errorCode: null, verification: summary({ state: 'verified', freshUntil: '2026-12-10T00:00:00Z' }),
    })
    renderOwnership(summary({ state: 'pending' }))

    await userEvent.click(screen.getByRole('button', { name: 'Проверить' }))

    expect(await screen.findByText('Владение сайтом подтверждено.')).toBeInTheDocument()
    expect(screen.getByText(/Подтверждён, действует до/)).toBeInTheDocument()
  })

  it.each([
    ['token_mismatch', /другой токен/],
    ['bad_content_type', /text\/plain/],
    ['http_404', /статусом 404/],
    ['fetch_dns_failure', /DNS/],
    ['challenge_expired', /истёк/],
  ])('неудачная проверка (%s) объясняется человеческим текстом в alert', async (errorCode, text) => {
    api.checkVerification.mockResolvedValue({ verified: false, errorCode, verification: summary({ state: 'pending', lastErrorCode: errorCode }) })
    renderOwnership(summary({ state: 'pending' }))

    await userEvent.click(screen.getByRole('button', { name: 'Проверить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('429 блокирует кнопку и показывает время ожидания из Retry-After', async () => {
    api.checkVerification.mockRejectedValue(httpError(429, { detail: { error: 'check_too_frequent', message: 'x' } }, { 'retry-after': '7' }))
    renderOwnership(summary({ state: 'pending' }))

    await userEvent.click(screen.getByRole('button', { name: 'Проверить' }))

    expect(await screen.findByText('Слишком частые запросы. Повторите через 7 с.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Повторить через 7 с' })).toBeDisabled()
  })

  it('409 no_pending_challenge подсказывает выдать файл заново', async () => {
    api.checkVerification.mockRejectedValue(httpError(409, { detail: { error: 'no_pending_challenge', message: 'x' } }))
    renderOwnership(summary({ state: 'pending' }))

    await userEvent.click(screen.getByRole('button', { name: 'Проверить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Сначала выдайте новый файл')
  })

  it('403 insufficient_role показывает понятную ошибку', async () => {
    api.requestVerificationChallenge.mockRejectedValue(httpError(403, { detail: { error: 'insufficient_role', message: 'x' } }))
    renderOwnership()

    await userEvent.click(screen.getByRole('button', { name: 'Получить файл подтверждения' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Недостаточно прав')
  })

  it('отзыв требует подтверждения и возвращает в «Не подтверждён»', async () => {
    api.revokeVerification.mockResolvedValue(undefined)
    api.fetchVerification.mockResolvedValue(summary({ state: 'unverified' }))
    renderOwnership(summary({ state: 'verified', freshUntil: '2026-12-10T00:00:00Z' }))

    await userEvent.click(screen.getByRole('button', { name: 'Отозвать подтверждение' }))
    expect(api.revokeVerification).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Да, отозвать' }))

    await waitFor(() => expect(api.revokeVerification).toHaveBeenCalledWith('p1'))
    expect(await screen.findByText('Не подтверждён')).toBeInTheDocument()
  })

  it('во время запроса кнопки заблокированы: двойной клик не отправляет второй запрос', async () => {
    let resolveChallenge: (value: unknown) => void = () => undefined
    api.requestVerificationChallenge.mockReturnValue(new Promise((resolve) => { resolveChallenge = resolve }))
    renderOwnership()

    const button = screen.getByRole('button', { name: 'Получить файл подтверждения' })
    await userEvent.click(button)
    await userEvent.click(screen.getByRole('button', { name: 'Готовим файл...' }))

    expect(api.requestVerificationChallenge).toHaveBeenCalledTimes(1)
    resolveChallenge({ fileUrl: 'u', fileBody: 'b', expiresAt: null, verification: summary({ state: 'pending' }) })
    await screen.findByText('b')
  })
})
