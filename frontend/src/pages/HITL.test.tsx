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
}))

vi.mock('../api/hitlAPI', () => ({
  fetchHITLTasks: apiMocks.fetchHITLTasks,
  approveTask: apiMocks.approveTask,
  rejectTask: apiMocks.rejectTask,
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
  })

  it('renders pending diff data and approves a task', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([
      {
        id: 'approval-1',
        taskId: 'task-1',
        projectId: 'project-1',
        status: 'pending',
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

    renderHITL()

    expect(await screen.findByText('Meta title update')).toBeInTheDocument()
    expect(screen.getByText('project-1')).toBeInTheDocument()
    expect(screen.getByText('https://example.com/page')).toBeInTheDocument()
    expect(screen.getByTestId('diff-viewer')).toHaveTextContent('Old title')
    expect(screen.getByTestId('diff-viewer')).toHaveTextContent('New title')

    await userEvent.type(screen.getByLabelText(/Комментарий/i), 'Approved in test')
    await userEvent.click(screen.getByRole('button', { name: /Одобрить/i }))

    await waitFor(() => {
      expect(apiMocks.approveTask).toHaveBeenCalledWith({
        taskId: 'task-1',
        approved: true,
        comment: 'Approved in test',
      })
    })
  })

  it('shows empty state when there are no pending tasks', async () => {
    apiMocks.fetchHITLTasks.mockResolvedValueOnce([])

    renderHITL()

    expect(await screen.findByText(/Нет ожидающих HITL/i)).toBeInTheDocument()
  })
})
