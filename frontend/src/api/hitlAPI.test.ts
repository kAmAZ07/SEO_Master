import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = {
  get: vi.fn(),
  post: vi.fn(),
}

vi.mock('./axiosConfig', () => ({
  default: apiMock,
}))

describe('hitlAPI', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
    apiMock.post.mockReset()
  })

  it('normalizes HITL task payloads from the gateway', async () => {
    const { fetchHITLTasks } = await import('./hitlAPI')
    apiMock.get.mockResolvedValueOnce({
      data: {
        approvals: [
          {
            id: 'approval-1',
            task_id: 'task-1',
            project_id: 'project-1',
            status: 'PENDING',
            diff_data: {
              before: { title: 'Old title' },
              after: { title: 'New title' },
            },
            impact_score: '72.5',
            recommendation: 'Review title update',
            metadata: { correlation_id: 'corr-1' },
            task: {
              id: 'task-1',
              title: 'Meta title update',
              url: 'https://example.com/page',
              task_type: 'UPDATE_META',
            },
          },
        ],
      },
    })

    const tasks = await fetchHITLTasks()

    expect(apiMock.get).toHaveBeenCalledWith('/hitl/tasks', {
      params: { status_filter: 'pending', limit: 50 },
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0]).toMatchObject({
      id: 'approval-1',
      taskId: 'task-1',
      projectId: 'project-1',
      status: 'pending',
      impactScore: 72.5,
      recommendation: 'Review title update',
      diffData: {
        before: { title: 'Old title' },
        after: { title: 'New title' },
      },
      task: {
        id: 'task-1',
        title: 'Meta title update',
        url: 'https://example.com/page',
        taskType: 'UPDATE_META',
      },
    })
  })

  it('sends approve and reject decisions to the expected endpoints', async () => {
    const { approveTask, rejectTask } = await import('./hitlAPI')
    apiMock.post.mockResolvedValue({})

    await approveTask({ taskId: 'task-1', approved: true, comment: 'Looks good' })
    await rejectTask({ taskId: 'task-2', approved: false, comment: 'Needs rewrite' })

    expect(apiMock.post).toHaveBeenNthCalledWith(1, '/hitl/tasks/task-1/approve', {
      comment: 'Looks good',
    })
    expect(apiMock.post).toHaveBeenNthCalledWith(2, '/hitl/tasks/task-2/reject', {
      comment: 'Needs rewrite',
    })
  })
})

