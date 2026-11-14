import { configureStore } from '@reduxjs/toolkit'
import { Provider } from 'react-redux'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import hitlReducer from '../store/slices/hitlSlice'
import HITL from './HITL'

const apiMocks = vi.hoisted(() => ({
  fetchHITLTasks: vi.fn(),
  approveTask: vi.fn(),
  rejectTask: vi.fn(),
  fetchTaskDetails: vi.fn(),
  applyTask: vi.fn(),
  cancelOperation: vi.fn(),
  rollbackOperation: vi.fn(),
}))

vi.mock('../api/hitlAPI', () => ({
  fetchHITLTasks: apiMocks.fetchHITLTasks,
  approveTask: apiMocks.approveTask,
  rejectTask: apiMocks.rejectTask,
  fetchTaskDetails: apiMocks.fetchTaskDetails,
  applyTask: apiMocks.applyTask,
  cancelOperation: apiMocks.cancelOperation,
  rollbackOperation: apiMocks.rollbackOperation,
}))

vi.mock('react-diff-viewer-continued', () => ({
  default: ({ oldValue, newValue }: { oldValue: string; newValue: string }) => (
    <div data-testid="diff-viewer">
      <pre>{oldValue}</pre>
      <pre>{newValue}</pre>
    </div>
  ),
  DiffMethod: { WORDS: 'WORDS' },
}))

const renderHITL = () => {
  const store = configureStore({
    reducer: {
      hitl: hitlReducer,
    },
  })

  render(
    <Provider store={store}>
      <HITL />
    </Provider>,
  )

  return store
}

describe('HITL page', () => {
  beforeEach(() => {
    apiMocks.fetchHITLTasks.mockReset()
    apiMocks.approveTask.mockReset()
    apiMocks.rejectTask.mockReset()
    apiMocks.fetchTaskDetails.mockReset()
    apiMocks.applyTask.mockReset()
    apiMocks.cancelOperation.mockReset()
    apiMocks.rollbackOperation.mockReset()
  })

  it('requires an explicit rollback reason and keeps one operation after a lost response', async () => {
    const task = { id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'original-operation', state: 'applied_unverified', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) } }
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([task])
    apiMocks.rollbackOperation.mockRejectedValueOnce(new Error('Lost response')).mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce({ ...task, execution: { ...task.execution, state: 'rolled_back',
      rollback: { operationId: 'server-rollback', state: 'rollback_applied_unverified', reason: 'Restore', verification: null } } })
    renderHITL()
    const button = await screen.findByRole('button', { name: 'Подтвердить откат к прежним значениям' })
    expect(button).toBeDisabled()
    expect(apiMocks.rollbackOperation).not.toHaveBeenCalled()
    await userEvent.type(screen.getByLabelText('Причина отката'), 'Restore')
    await userEvent.click(button)
    await waitFor(() => expect(apiMocks.rollbackOperation).toHaveBeenCalledTimes(1))
    const operationId = apiMocks.rollbackOperation.mock.calls[0][2]
    await waitFor(() => expect(button).toBeEnabled())
    expect(screen.getByLabelText('Причина отката')).toBeDisabled()
    await userEvent.click(button)
    await waitFor(() => expect(apiMocks.rollbackOperation).toHaveBeenLastCalledWith('task-1', 'original-operation', operationId, 'Restore'))
    expect(await screen.findByText(/Откат записан. Независимая проверка ещё не выполнена/)).toBeInTheDocument()
    expect(screen.queryByText('Откат подтверждён независимым чтением публичной страницы.')).not.toBeInTheDocument()
    expect(apiMocks.applyTask).not.toHaveBeenCalled()
  })

  it('cancels only the unbegun rollback and allows a new explicitly reviewed request', async () => {
    const task = { id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: {}, after: {} }, metadata: {},
      execution: { operationId: 'original-operation', state: 'verified', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
        rollback: { operationId: 'failed-rollback', state: 'failed_before_apply', reason: 'Restore', verification: null } } }
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([task])
    apiMocks.cancelOperation.mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce({ ...task, execution: { ...task.execution,
      rollback: { ...task.execution.rollback, state: 'cancelled' } } })
    apiMocks.rollbackOperation.mockRejectedValueOnce(new Error('Lost response'))
    renderHITL()
    await userEvent.click(await screen.findByRole('button', { name: 'Отменить откат до записи' }))
    await waitFor(() => expect(apiMocks.cancelOperation).toHaveBeenCalledWith('task-1', 'failed-rollback'))
    const reason = await screen.findByLabelText('Причина отката')
    await userEvent.type(reason, 'Reviewed again')
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить откат к прежним значениям' }))
    await waitFor(() => expect(apiMocks.rollbackOperation).toHaveBeenCalledTimes(1))
    expect(apiMocks.rollbackOperation.mock.calls[0][2]).not.toBe('failed-rollback')
    expect(apiMocks.applyTask).not.toHaveBeenCalled()
  })

  it('keeps an oversized UTF8 reason editable without creating a request', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([{ id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      diffData: { before: {}, after: {} }, metadata: {}, execution: { operationId: 'original', state: 'verified' } }])
    renderHITL()
    const reason = await screen.findByLabelText('Причина отката')
    await userEvent.click(reason)
    await userEvent.paste('Я'.repeat(600))
    expect(screen.getByRole('button', { name: 'Подтвердить откат к прежним значениям' })).toBeDisabled()
    expect(reason).toBeEnabled()
    expect(apiMocks.rollbackOperation).not.toHaveBeenCalled()
    await userEvent.clear(reason)
    await userEvent.type(reason, 'Restore')
    expect(screen.getByRole('button', { name: 'Подтвердить откат к прежним значениям' })).toBeEnabled()
  })

  it('renders pending diff data and approves a task', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([
      {
        id: 'approval-1',
        taskId: 'task-1',
        projectId: 'project-1',
        status: 'pending',
        authorizationStatus: 'pending',
        recommendationVersionId: 'version-1',
        contentHash: 'a'.repeat(64),
        diffData: {
          before: { title: 'Old title' },
          after: { title: 'New title' },
        },
        impactScore: 83,
        recommendation: 'Review generated SEO changes before deployment',
        approvedBy: null,
        approvedAt: null,
        rejectedBy: null,
        rejectedAt: null,
        rejectionReason: null,
        metadata: { url: 'https://example.com/page', correlation_id: 'corr-1' },
        task: {
          id: 'task-1',
          title: 'Meta title update',
          description: 'Review title and description',
          url: 'https://example.com/page',
          taskType: 'UPDATE_META',
          metadata: {},
        },
      },
    ])
    apiMocks.approveTask.mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce({
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true,
      recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old title' }, after: { title: 'New title' } }, metadata: {},
    })

    renderHITL()

    expect(await screen.findByText('Meta title update')).toBeInTheDocument()
    expect(screen.getByText('project-1')).toBeInTheDocument()
    expect(screen.getByText('https://example.com/page')).toBeInTheDocument()
    expect(screen.getByLabelText('Просматриваемая версия предложения')).toHaveTextContent('version-1')
    expect(screen.getByLabelText('Просматриваемая версия предложения')).toHaveTextContent('a'.repeat(64))
    expect(screen.getByTestId('diff-viewer')).toHaveTextContent('Old title')
    expect(screen.getByTestId('diff-viewer')).toHaveTextContent('New title')

    await userEvent.type(screen.getByLabelText(/Комментарий/i), 'Approved in test')
    await userEvent.click(screen.getByRole('button', { name: /Одобрить/i }))

    await waitFor(() => {
      expect(apiMocks.approveTask).toHaveBeenCalledWith({
        taskId: 'task-1',
        approvalId: 'approval-1',
        recommendationVersionId: 'version-1',
        contentHash: 'a'.repeat(64),
        approved: true,
        comment: 'Approved in test',
      })
    })
    expect(apiMocks.applyTask).not.toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: 'Применить одобренные изменения' })).toBeEnabled()
  })

  it('shows empty state when there are no pending tasks', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([])

    renderHITL()

    expect(await screen.findByText(/Нет ожидающих HITL/i)).toBeInTheDocument()
  })

  it('prevents a decision for an invalidated preview', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([{
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'pending',
      authorizationStatus: 'invalidated', recommendationVersionId: 'old-version', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
    }])
    renderHITL()
    const button = await screen.findByRole('button', { name: /^Одобрить$/i })
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(apiMocks.approveTask).not.toHaveBeenCalled()
    expect(await screen.findByText(/требуется актуальная проверенная версия/i)).toBeInTheDocument()
  })

  it('reconciles unknown publication using the original operation and never calls approve', async () => {
    const task = {
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'invalidated', managedTarget: true,
      recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'same-operation', state: 'outcome_unknown', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) },
    }
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([task])
    apiMocks.applyTask.mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce({ ...task, execution: { ...task.execution, state: 'applied_unverified' } })
    renderHITL()
    await userEvent.click(await screen.findByRole('button', { name: 'Проверить результат публикации' }))
    await waitFor(() => expect(apiMocks.applyTask).toHaveBeenCalledWith(expect.objectContaining({ taskId: 'task-1',
      recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }), 'same-operation'))
    expect(await screen.findByRole('status')).toHaveTextContent('Независимая проверка ещё не выполнена')
    expect(apiMocks.approveTask).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Применить одобренные изменения' })).not.toBeInTheDocument()
  })

  it('keeps a new operation ID after lost response while an expired summary is still displayed', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([{
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'expired-operation', state: 'expired', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) },
    }])
    apiMocks.applyTask.mockRejectedValue(new Error('response lost'))
    renderHITL()
    const button = await screen.findByRole('button', { name: 'Применить одобренные изменения' })
    await userEvent.click(button)
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    await waitFor(() => expect(apiMocks.applyTask).toHaveBeenCalledTimes(2))
    expect(apiMocks.applyTask.mock.calls[0][1]).not.toBe('expired-operation')
    expect(apiMocks.applyTask.mock.calls[1][1]).toBe(apiMocks.applyTask.mock.calls[0][1])
  })

  it('shows spent recovery budget and never offers another publication after reconciliation stops', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([{
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'same-operation', state: 'needs_reconciliation', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
        recovery: { attempts: 4, maxAttempts: 4, lastError: 'operation_not_found', stopReason: 'attempts_exhausted' } },
    }])
    renderHITL()
    expect(await screen.findByRole('status')).toHaveTextContent('Требуется ручной разбор')
    expect(screen.getByText(/Попытки сверки: 4 из 4/)).toHaveTextContent('Лимит попыток исчерпан')
    expect(screen.queryByRole('button', { name: 'Применить одобренные изменения' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Проверить результат публикации' })).not.toBeInTheDocument()
    expect(apiMocks.applyTask).not.toHaveBeenCalled()
  })

  it('continues queued preparation with the saved operation ID', async () => {
    const task = {
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'queued-operation', state: 'queued', prepared: false, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) },
    }
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([task])
    apiMocks.applyTask.mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce(task)
    renderHITL()
    await userEvent.click(await screen.findByRole('button', { name: 'Продолжить подготовку публикации' }))
    await waitFor(() => expect(apiMocks.applyTask).toHaveBeenCalledWith(expect.anything(), 'queued-operation'))
    expect(apiMocks.approveTask).not.toHaveBeenCalled()
  })

  it('shows pre-write conflict and requires explicit cancellation before another operation', async () => {
    const task = {
      id: 'approval-1', taskId: 'task-1', projectId: 'project-1', status: 'approved',
      authorizationStatus: 'approved', managedTarget: true, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
      diffData: { before: { title: 'Old' }, after: { title: 'New' } }, metadata: {},
      execution: { operationId: 'conflicted-operation', state: 'conflict', prepared: false, recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64),
        preparation: { attempts: 1, maxAttempts: 4, errorCode: 'cms_precondition_conflict' } },
    }
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([task])
    apiMocks.cancelOperation.mockResolvedValueOnce(undefined)
    apiMocks.fetchTaskDetails.mockResolvedValueOnce({ ...task, execution: { ...task.execution, state: 'cancelled' } })
    renderHITL()
    expect(await screen.findByRole('status')).toHaveTextContent('Команда записи не отправлена')
    expect(screen.queryByRole('button', { name: 'Применить одобренные изменения' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Отменить операцию до публикации' }))
    await waitFor(() => expect(apiMocks.cancelOperation).toHaveBeenCalledWith('task-1', 'conflicted-operation'))
    expect(await screen.findByRole('button', { name: 'Применить одобренные изменения' })).toBeEnabled()
    expect(apiMocks.applyTask).not.toHaveBeenCalled()
  })
})
