import type { ReactNode } from 'react'
import { Provider } from 'react-redux'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAppStore } from '../store/store'
import { AUDIT_POLL_INTERVAL_MS, AUDIT_POLL_MAX_FAILURES, useAuditPolling } from './useAuditPolling'
import type { AuditStatus, AuditWorkflowStatus } from '../types/audit'

const apiMock = vi.hoisted(() => ({ getAuditStatus: vi.fn() }))
vi.mock('../api/publicAuditAPI', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/publicAuditAPI')>()),
  getAuditStatus: apiMock.getAuditStatus,
}))

const audit = (uid: string, status: AuditWorkflowStatus): AuditStatus => ({
  uid, id: uid, url: 'https://example.com', status, progress: 10, createdAt: '2026-11-10T00:00:00Z',
  score: null, issues: { passed: 0, warnings: 0, errors: 0 }, details: [],
})

const networkError = () => Object.assign(new Error('Network Error'), { isAxiosError: true, code: 'ERR_NETWORK' })

describe('useAuditPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMock.getAuditStatus.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const setup = (uid: string | undefined) => {
    const store = createAppStore()
    const wrapper = ({ children }: { children: ReactNode }) => <Provider store={store}>{children}</Provider>
    const hook = renderHook(({ id }: { id: string | undefined }) => useAuditPolling(id), { wrapper, initialProps: { id: uid } })
    return { store, ...hook }
  }

  const tick = async (ms = AUDIT_POLL_INTERVAL_MS) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  it('опрашивает, пока аудит выполняется, и останавливается на конечном статусе', async () => {
    apiMock.getAuditStatus
      .mockResolvedValueOnce(audit('a1', 'queued'))
      .mockResolvedValueOnce(audit('a1', 'running'))
      .mockResolvedValueOnce(audit('a1', 'partial'))
    const { store } = setup('a1')
    await tick(0)
    await tick()
    await tick()
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(3)
    expect(store.getState().audit.currentAudit?.status).toBe('partial')

    await tick(AUDIT_POLL_INTERVAL_MS * 5)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(3)
  })

  it.each<AuditWorkflowStatus>(['cancelled', 'expired', 'failed', 'unknown'])('прекращает опрос на статусе %s', async (status) => {
    apiMock.getAuditStatus.mockResolvedValue(audit('a1', status))
    setup('a1')
    await tick(0)
    await tick(AUDIT_POLL_INTERVAL_MS * 4)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(1)
  })

  it('прекращает опрос при размонтировании', async () => {
    apiMock.getAuditStatus.mockResolvedValue(audit('a1', 'running'))
    const { unmount } = setup('a1')
    await tick(0)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(1)

    unmount()
    await tick(AUDIT_POLL_INTERVAL_MS * 5)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(1)
  })

  it('размонтирование отменяет запрос в пути и не планирует следующий после его ответа', async () => {
    let signal: AbortSignal | undefined
    let resolveRequest: (value: AuditStatus) => void = () => undefined
    apiMock.getAuditStatus.mockImplementation((_uid: string, requestSignal?: AbortSignal) => {
      signal = requestSignal
      return new Promise<AuditStatus>((resolve) => { resolveRequest = resolve })
    })
    const { unmount } = setup('a1')
    await tick(0)
    expect(signal?.aborted).toBe(false)

    unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      resolveRequest(audit('a1', 'running'))
    })
    await tick(AUDIT_POLL_INTERVAL_MS * 5)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(1)
  })

  it('при смене аудита опрашивает новый uid и не трогает прежний', async () => {
    apiMock.getAuditStatus.mockImplementation(async (uid: string) => audit(uid, 'running'))
    const { rerender, store } = setup('a1')
    await tick(0)
    rerender({ id: 'a2' })
    await tick(0)
    apiMock.getAuditStatus.mockClear()
    await tick()

    expect(apiMock.getAuditStatus.mock.calls.every(([uid]) => uid === 'a2')).toBe(true)
    expect(store.getState().audit.activeUid).toBe('a2')
  })

  it('поздний ответ по прежнему uid не затирает новый', async () => {
    let resolveFirst: (value: AuditStatus) => void = () => undefined
    apiMock.getAuditStatus.mockImplementation((uid: string) =>
      uid === 'a1' ? new Promise<AuditStatus>((resolve) => { resolveFirst = resolve }) : Promise.resolve(audit(uid, 'running')),
    )
    const { rerender, store } = setup('a1')
    await tick(0)
    rerender({ id: 'a2' })
    await tick(0)
    await act(async () => {
      resolveFirst(audit('a1', 'completed'))
    })

    expect(store.getState().audit.currentAudit?.uid).toBe('a2')
  })

  it('после серии сбоев останавливается и возобновляется по «Повторить»', async () => {
    apiMock.getAuditStatus.mockRejectedValue(networkError())
    const { result, store } = setup('a1')
    for (let i = 0; i < AUDIT_POLL_MAX_FAILURES; i += 1) {
      await tick(i === 0 ? 0 : AUDIT_POLL_INTERVAL_MS)
    }
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(AUDIT_POLL_MAX_FAILURES)
    expect(result.current.gaveUp).toBe(true)
    expect(store.getState().audit.polling).toBe(false)

    await tick(AUDIT_POLL_INTERVAL_MS * 3)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(AUDIT_POLL_MAX_FAILURES)

    apiMock.getAuditStatus.mockResolvedValue(audit('a1', 'completed'))
    act(() => result.current.retry())
    await tick(0)
    expect(result.current.gaveUp).toBe(false)
    expect(store.getState().audit.currentAudit?.status).toBe('completed')
  })

  it('постоянная ошибка (404) останавливает опрос сразу', async () => {
    apiMock.getAuditStatus.mockRejectedValue(
      Object.assign(new Error('nf'), { isAxiosError: true, response: { status: 404 } }),
    )
    const { store, result } = setup('a1')
    await tick(0)
    await tick(AUDIT_POLL_INTERVAL_MS * 4)
    expect(apiMock.getAuditStatus).toHaveBeenCalledTimes(1)
    expect(store.getState().audit.errorKind).toBe('not_found')
    expect(result.current.gaveUp).toBe(false)
  })

  it('без uid ничего не запрашивает', async () => {
    setup(undefined)
    await tick(AUDIT_POLL_INTERVAL_MS * 3)
    expect(apiMock.getAuditStatus).not.toHaveBeenCalled()
  })
})
