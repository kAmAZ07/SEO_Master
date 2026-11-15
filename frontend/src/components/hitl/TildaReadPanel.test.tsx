import { beforeEach, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TildaReadPanel from './TildaReadPanel'

const mock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../../api/axiosConfig', () => ({ default: mock }))
const source = (project = 'project-1') => ({ project_id: project, can_sync: true, reader_state: 'active',
  callback_url: 'https://app.example/api/tilda/webhook/project/private-capability/published',
  capabilities: { mode: 'read_only_manual', write_status: 'write_unsupported', requests_per_hour: 150, required_plan: 'Business', configured_requests_per_hour: 150 }, jobs: [] })
beforeEach(() => vi.resetAllMocks())

it('reads only cached status on mount/refresh; upstream sync and callback disclosure are explicit', async () => {
  mock.get.mockResolvedValue({ data: source() }); mock.post.mockResolvedValue({ data: { status: 'queued' } })
  render(<TildaReadPanel projectId="project-1" />)
  const sync = await screen.findByRole('button', { name: 'Прочитать страницы Tilda' })
  expect(screen.getByText(/до 150 запросов/)).toBeVisible()
  expect(mock.post).not.toHaveBeenCalled()
  expect(screen.queryByLabelText('Секретный адрес callback Tilda')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Обновить сохранённый статус Tilda' }))
  expect(mock.post).not.toHaveBeenCalled()
  await userEvent.click(sync)
  await waitFor(() => expect(mock.post).toHaveBeenCalledWith('/projects/project-1/tilda/sync'))
  await userEvent.click(screen.getByRole('button', { name: 'Показать адрес callback' }))
  expect(screen.getByLabelText('Секретный адрес callback Tilda')).toHaveValue(source().callback_url)
  expect(document.querySelector('a')).toBeNull()
})

it('shows snapshots as inert JSON with empty, null and missing preserved', async () => {
  const value = { ...source(), jobs: [{ job_id: 'j', status: 'manual_required', created_at: 'now', error_code: null,
    result: { observed_at: 'now', pages: [{ page_id: '123', url: 'https://example.com/page', fields: { title: '', descr: null, html: '<img src=x onerror=alert(1)>' } }] } }] }
  mock.get.mockResolvedValue({ data: value })
  render(<TildaReadPanel projectId="project-1" />)
  expect(await screen.findByText(/"title": ""/)).toHaveTextContent('"descr": null')
  expect(document.querySelector('img')).toBeNull()
  expect(mock.post).not.toHaveBeenCalled()
})

it('does not offer sync or callback to a reader and rejects a write-enabled contract', async () => {
  mock.get.mockResolvedValueOnce({ data: { ...source(), can_sync: false, callback_url: null } })
  const { unmount } = render(<TildaReadPanel projectId="project-1" />)
  await waitFor(() => expect(mock.get).toHaveBeenCalledOnce())
  expect(screen.queryByRole('button', { name: 'Прочитать страницы Tilda' })).not.toBeInTheDocument()
  unmount()
  mock.get.mockResolvedValueOnce({ data: { ...source(), capabilities: { mode: 'auto_write' } } })
  render(<TildaReadPanel projectId="project-1" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Статус чтения недоступен')
})

it('ignores a late prior-project response even when the transport ignores abort', async () => {
  let resolveOld!: (value: unknown) => void
  mock.get.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
  mock.get.mockResolvedValueOnce({ data: { ...source('project-2'), callback_url: 'https://app.example/new' } })
  const { rerender } = render(<TildaReadPanel projectId="project-1" />)
  rerender(<TildaReadPanel projectId="project-2" />)
  await screen.findByRole('button', { name: 'Прочитать страницы Tilda' })
  await act(async () => resolveOld({ data: source() }))
  await userEvent.click(screen.getByRole('button', { name: 'Показать адрес callback' }))
  expect(screen.getByLabelText('Секретный адрес callback Tilda')).toHaveValue('https://app.example/new')
})

it('paginates cached pages without scheduling another upstream read', async () => {
  mock.get.mockImplementation((path, options) => Promise.resolve({ data: path.endsWith('/pages') ? {
    project_id: 'project-1', total: 51, observed_at: 'now', items: options.params.offset === 0
      ? Array.from({ length: 50 }, (_, i) => ({ page_id: String(i + 1), url: `https://example.com/p${i + 1}`, fields: {} }))
      : [{ page_id: '51', url: 'https://example.com/p51', fields: {} }],
  } : source() }))
  render(<TildaReadPanel projectId="project-1" />)
  await userEvent.click(await screen.findByRole('button', { name: 'Список сохранённых страниц Tilda' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Следующие страницы Tilda' }))
  expect(await screen.findByText('51: https://example.com/p51')).toBeVisible()
  expect(mock.get).toHaveBeenLastCalledWith('/projects/project-1/tilda/pages', { params: { limit: 50, offset: 50 } })
  await userEvent.click(screen.getByRole('button', { name: 'Предыдущие страницы Tilda' }))
  expect(await screen.findByText('1: https://example.com/p1')).toBeVisible()
  expect(mock.post).not.toHaveBeenCalled()
})

it('shows disabled processing explicitly and offers no upstream sync', async () => {
  mock.get.mockResolvedValue({ data: { ...source(), reader_state: 'disabled', can_sync: false } })
  render(<TildaReadPanel projectId="project-1" />)
  expect(await screen.findByText(/Обработчик чтения: отключён/)).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Прочитать страницы Tilda' })).not.toBeInTheDocument()
})
