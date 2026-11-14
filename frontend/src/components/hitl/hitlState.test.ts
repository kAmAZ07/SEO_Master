import { configureStore } from '@reduxjs/toolkit'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { HITLTask } from '../../types/hitl'
import reducer, { approveHITLTask, rejectHITLTask, loadHITLTasks } from '../../store/slices/hitlSlice'

const api = vi.hoisted(() => ({ fetchHITLTasks: vi.fn(), approveTask: vi.fn(), rejectTask: vi.fn(), fetchTaskDetails: vi.fn() }))
vi.mock('../../api/hitlAPI', () => api)

const task = { id: 'approval', taskId: 'task', projectId: 'project', status: 'pending',
  recommendationVersionId: 'version', contentHash: 'a'.repeat(64), diffData: { before: {}, after: {} }, metadata: {} } as HITLTask

describe('HITL stale read fences', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('does not replace a new approval result with a list captured before the decision', async () => {
    let resolveList!: (tasks: HITLTask[]) => void
    api.fetchHITLTasks.mockImplementationOnce(() => new Promise<HITLTask[]>(resolve => { resolveList = resolve }))
    api.approveTask.mockResolvedValue(undefined)
    api.fetchTaskDetails.mockResolvedValue({ ...task, status: 'approved' })
    const store = configureStore({ reducer: { hitl: reducer } })
    store.dispatch(loadHITLTasks.pending('initial', undefined))
    store.dispatch(loadHITLTasks.fulfilled([task], 'initial', undefined))
    const oldRead = store.dispatch(loadHITLTasks())
    await store.dispatch(approveHITLTask({ taskId: 'task', approvalId: 'approval', recommendationVersionId: 'version', contentHash: 'a'.repeat(64), approved: true }))
    resolveList([task])
    await oldRead
    expect(store.getState().hitl.tasks[0].status).toBe('approved')
  })

  it('ignores a late result or error from a previous list request', async () => {
    let resolveOld!: (tasks: HITLTask[]) => void
    api.fetchHITLTasks.mockImplementationOnce(() => new Promise<HITLTask[]>(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce([{ ...task, status: 'approved' }])
    const store = configureStore({ reducer: { hitl: reducer } })
    const oldRead = store.dispatch(loadHITLTasks())
    await store.dispatch(loadHITLTasks())
    resolveOld([task])
    await oldRead
    expect(store.getState().hitl.tasks[0].status).toBe('approved')
    store.dispatch(loadHITLTasks.rejected(new Error('old read'), 'old-id', undefined, 'old error'))
    expect(store.getState().hitl.error).toBeNull()
  })

  it('keeps a newer list version when the old command detail response arrives last', async () => {
    let resolveDetail!: (task: HITLTask) => void
    api.approveTask.mockResolvedValue(undefined)
    api.fetchTaskDetails.mockImplementationOnce(() => new Promise<HITLTask>(resolve => { resolveDetail = resolve }))
    api.fetchHITLTasks.mockResolvedValue([{ ...task, recommendationVersionId: 'new-version', contentHash: 'b'.repeat(64) }])
    const store = configureStore({ reducer: { hitl: reducer } })
    const command = store.dispatch(approveHITLTask({ taskId: 'task', approvalId: 'approval', recommendationVersionId: 'version', contentHash: 'a'.repeat(64), approved: true }))
    await vi.waitFor(() => expect(api.fetchTaskDetails).toHaveBeenCalled())
    await store.dispatch(loadHITLTasks())
    resolveDetail({ ...task, status: 'approved' })
    await command
    expect(store.getState().hitl.tasks[0].recommendationVersionId).toBe('new-version')
  })

  it('reads the authoritative rejection after a pending list was captured during the POST', async () => {
    let finishPost!: () => void
    api.rejectTask.mockImplementationOnce(() => new Promise<void>(resolve => { finishPost = resolve }))
    api.fetchHITLTasks.mockResolvedValue([task])
    api.fetchTaskDetails.mockResolvedValue({ ...task, status: 'rejected', rejectionReason: 'Wrong title' })
    const store = configureStore({ reducer: { hitl: reducer } })
    const command = store.dispatch(rejectHITLTask({ taskId: 'task', approvalId: 'approval', recommendationVersionId: 'version', contentHash: 'a'.repeat(64), approved: false }))
    await store.dispatch(loadHITLTasks())
    finishPost()
    await command
    expect(store.getState().hitl.tasks[0]).toMatchObject({ status: 'rejected', rejectionReason: 'Wrong title' })
  })

  it('ignores a list started during POST that resolves between detail-read start and completion', async () => {
    let finishPost!: () => void
    let resolveList!: (tasks: HITLTask[]) => void
    let resolveDetail!: (task: HITLTask) => void
    api.approveTask.mockImplementationOnce(() => new Promise<void>(resolve => { finishPost = resolve }))
    api.fetchHITLTasks.mockImplementationOnce(() => new Promise<HITLTask[]>(resolve => { resolveList = resolve }))
    api.fetchTaskDetails.mockImplementationOnce(() => new Promise<HITLTask>(resolve => { resolveDetail = resolve }))
    const store = configureStore({ reducer: { hitl: reducer } })
    store.dispatch(loadHITLTasks.pending('initial', undefined))
    store.dispatch(loadHITLTasks.fulfilled([task], 'initial', undefined))
    const command = store.dispatch(approveHITLTask({ taskId: 'task', approvalId: 'approval', recommendationVersionId: 'version', contentHash: 'a'.repeat(64), approved: true }))
    const oldList = store.dispatch(loadHITLTasks())
    finishPost()
    await vi.waitFor(() => expect(api.fetchTaskDetails).toHaveBeenCalled())
    resolveList([task])
    await oldList
    resolveDetail({ ...task, status: 'approved' })
    await command
    expect(store.getState().hitl.tasks[0].status).toBe('approved')
  })

  it('keeps a newer list in flight when an older command detail completes first', async () => {
    let resolveDetail!: (task: HITLTask) => void
    let resolveList!: (tasks: HITLTask[]) => void
    api.approveTask.mockResolvedValue(undefined)
    api.fetchTaskDetails.mockImplementationOnce(() => new Promise<HITLTask>(resolve => { resolveDetail = resolve }))
    api.fetchHITLTasks.mockImplementationOnce(() => new Promise<HITLTask[]>(resolve => { resolveList = resolve }))
    const store = configureStore({ reducer: { hitl: reducer } })
    store.dispatch(loadHITLTasks.pending('initial', undefined))
    store.dispatch(loadHITLTasks.fulfilled([task], 'initial', undefined))
    const command = store.dispatch(approveHITLTask({ taskId: 'task', approvalId: 'approval', recommendationVersionId: 'version', contentHash: 'a'.repeat(64), approved: true }))
    await vi.waitFor(() => expect(api.fetchTaskDetails).toHaveBeenCalled())
    const newerList = store.dispatch(loadHITLTasks())
    resolveDetail({ ...task, status: 'approved' })
    await command
    resolveList([{ ...task, recommendationVersionId: 'new-version', contentHash: 'b'.repeat(64) }])
    await newerList
    expect(store.getState().hitl.tasks[0].recommendationVersionId).toBe('new-version')
  })
})
