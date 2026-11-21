import { beforeEach, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TildaManualCheck from './TildaManualCheck'
import type { HITLTask } from '../../types/hitl'

const mock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../../api/axiosConfig', () => ({ default: mock }))
const task: HITLTask = { id: 'approval', taskId: 'task', projectId: 'project', status: 'approved', authorizationStatus: 'approved',
  recommendationVersionId: 'version', contentHash: 'a'.repeat(64), tildaTarget: true, managedTarget: false,
  manualCheckFields: ['title'],
  metadata: {}, diffData: { before: {}, after: {} }, task: { id: 'task', title: 'Manual edit', url: 'https://example.com/page', metadata: {} } }
const result = (id: string) => ({ check_id: id, task_id: task.taskId, project_id: task.projectId,
  recommendation_version_id: task.recommendationVersionId, content_hash: task.contentHash, receipt: null,
  status: 'manual_verified', result: { error_code: null, observation: { url: task.task?.url, final_url: task.task?.url, observed_at: 'now', status: 200 },
    checks: [{ field: 'http', matches: true }, { field: 'title', matches: true }] } })
beforeEach(() => vi.resetAllMocks())

it('keeps the same check after a lost response and reconciles only by GET', async () => {
  mock.post.mockRejectedValue(new Error('lost response'))
  render(<TildaManualCheck task={task} />)
  expect(mock.post).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Проверить ручное внедрение независимо' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Результат проверки не подтверждён')
  const payload = mock.post.mock.calls[0][1]
  expect(payload).toMatchObject({ approval_id: task.id, recommendation_version_id: task.recommendationVersionId, content_hash: task.contentHash })
  mock.get.mockResolvedValue({ data: result(payload.check_id) })
  await userEvent.click(screen.getByRole('button', { name: 'Прочитать статус ручной проверки' }))
  expect(await screen.findByRole('status')).toHaveTextContent('CMS-квитанция отсутствует')
  expect(mock.get).toHaveBeenCalledWith(`/hitl/tasks/task/manual-checks/${payload.check_id}`)
  expect(mock.post).toHaveBeenCalledOnce()
})

it('requires approval even though the action only compares the public page', () => {
  render(<TildaManualCheck task={{ ...task, authorizationStatus: 'pending' }} />)
  expect(screen.getByRole('button', { name: 'Проверить ручное внедрение независимо' })).toBeDisabled()
  expect(mock.post).not.toHaveBeenCalled()
})

it.each(['wrong_url', 'false_predicate', 'only_http', 'no_http', 'duplicate', 'partial', 'automatic_state', 'receipt'])('rejects malformed success: %s', async kind => {
  mock.post.mockImplementation((_path, command) => {
    const value = result(command.check_id)
    if (kind === 'wrong_url') value.result.observation.url = 'https://other.example/'
    if (kind === 'false_predicate') value.result.checks[1].matches = false
    if (kind === 'only_http') value.result.checks = value.result.checks.slice(0, 1)
    if (kind === 'no_http') value.result.checks = value.result.checks.slice(1)
    if (kind === 'duplicate') value.result.checks.push(value.result.checks[1])
    if (kind === 'automatic_state') value.status = 'verified'
    if (kind === 'receipt') return Promise.resolve({ data: { ...value, receipt: { outcome: 'applied' } } })
    return Promise.resolve({ data: value })
  })
  render(<TildaManualCheck task={kind === 'partial' ? { ...task, manualCheckFields: ['title', 'description'] } : task} />)
  await userEvent.click(screen.getByRole('button', { name: 'Проверить ручное внедрение независимо' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Результат проверки не подтверждён')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('allows a retry with the same ID only after authoritative status says no check was created', async () => {
  mock.post.mockRejectedValueOnce({ response: { status: 409 } })
  mock.get.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404, data: { error: { message: 'manual_check_not_found' } } } })
  render(<TildaManualCheck task={task} />)
  await userEvent.click(screen.getByRole('button', { name: 'Проверить ручное внедрение независимо' }))
  await screen.findByRole('alert')
  const payload = mock.post.mock.calls[0][1]
  await userEvent.click(screen.getByRole('button', { name: 'Прочитать статус ручной проверки' }))
  mock.post.mockResolvedValueOnce({ data: result(payload.check_id) })
  await userEvent.click(await screen.findByRole('button', { name: 'Повторить запрос той же проверки' }))
  expect(await screen.findByRole('status')).toHaveTextContent('CMS-квитанция отсутствует')
  expect(mock.post.mock.calls[1][1]).toEqual(payload)
})
