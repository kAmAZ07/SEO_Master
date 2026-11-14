import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { HITLOperation } from '../../types/hitl'
import OperationDetails from './OperationDetails'

const api = vi.hoisted(() => ({ fetchOperation: vi.fn() }))
vi.mock('../../api/hitlAPI', () => ({ fetchOperation: api.fetchOperation }))

const context = { taskId: 'task', projectId: 'project', recommendationVersionId: 'version', contentHash: 'a'.repeat(64) }
const operation: HITLOperation = {
  operationId: 'original', state: 'applied_unverified', operationType: 'apply', recordHash: 'b'.repeat(64), requestHash: 'c'.repeat(64),
  before: { fields: { title: { state: 'missing' }, description: { state: 'value', value: '' }, jsonld: null } },
  after: { fields: { title: { state: 'value', value: '<script>unsafe()</script>' } } },
  receipt: { receiptId: 'd'.repeat(64), operationId: 'original', outcome: 'applied_unverified', appliedAt: '2026-11-14T10:00:00Z',
    requestHash: 'c'.repeat(64), beforeDigest: 'e'.repeat(64), afterDigest: 'f'.repeat(64), appliedFields: ['title'] },
  verification: null,
}

describe('OperationDetails', () => {
  beforeEach(() => api.fetchOperation.mockReset())

  it('reads only after an explicit open and shows exact escaped snapshots without claiming verified', async () => {
    api.fetchOperation.mockResolvedValue(operation)
    const { container } = render(<OperationDetails context={context} operationId="original" label="Данные публикации" />)
    expect(api.fetchOperation).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Данные публикации' }))
    expect(await screen.findByLabelText('Квитанция CMS')).toHaveTextContent('applied_unverified')
    expect(screen.getByText(/Квитанция подтверждает результат CMS/)).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    await userEvent.click(screen.getByText('Снимок до записи'))
    expect(screen.getByText(/"state": "missing"/)).toHaveTextContent('"value": ""')
    expect(screen.getByText(/"jsonld": null/)).toBeInTheDocument()
    await userEvent.click(screen.getByText('Снимок после записи'))
    expect(screen.getByText(/<script>unsafe\(\)<\/script>/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные операции' }))
    await waitFor(() => expect(api.fetchOperation).toHaveBeenCalledTimes(2))
  })

  it('aborts and ignores a late response after switching to another operation', async () => {
    let resolveOld!: (value: HITLOperation) => void
    api.fetchOperation.mockImplementationOnce(() => new Promise<HITLOperation>(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ ...operation, operationId: 'rollback', receipt: { ...operation.receipt!, receiptId: 'new-receipt' } })
    const view = render(<OperationDetails context={context} operationId="original" label="Данные" />)
    await userEvent.click(screen.getByRole('button', { name: 'Данные' }))
    const oldSignal = api.fetchOperation.mock.calls[0][2] as AbortSignal
    view.rerender(<OperationDetails context={context} operationId="rollback" label="Данные" />)
    expect(await screen.findByLabelText('Квитанция CMS')).toHaveTextContent('new-receipt')
    expect(oldSignal.aborted).toBe(true)
    await act(async () => resolveOld(operation))
    expect(screen.getByLabelText('Квитанция CMS')).toHaveTextContent('new-receipt')
    expect(screen.queryByText(operation.receipt!.receiptId)).not.toBeInTheDocument()
  })

  it('keeps an unavailable or denied read separate from success and permits a read retry', async () => {
    api.fetchOperation.mockRejectedValueOnce(new Error('forbidden')).mockResolvedValueOnce({ ...operation, receipt: null })
    render(<OperationDetails context={context} operationId="original" label="Данные" />)
    await userEvent.click(screen.getByRole('button', { name: 'Данные' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('недоступны')
    expect(screen.queryByLabelText('Квитанция CMS')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Обновить данные операции' }))
    expect(await screen.findByText(/Подтверждённой квитанции CMS пока нет/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
