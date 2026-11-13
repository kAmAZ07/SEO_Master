import { describe, expect, it } from 'vitest'

import reducer, { cancelAuditRequest, pollAuditStatus, selectAudit, startAudit } from './auditSlice'
import type { AuditStatus, AuditWorkflowStatus } from '@/types/audit'

const audit = (uid: string, status: AuditWorkflowStatus = 'running'): AuditStatus => ({
  uid, id: uid, url: 'https://example.com', status, progress: 10, createdAt: '2026-11-10T00:00:00Z',
  score: null, issues: { passed: 0, warnings: 0, errors: 0 }, details: [],
})

const initial = reducer(undefined, { type: 'init' })
const polled = (uid: string, status: AuditWorkflowStatus) => pollAuditStatus.fulfilled(audit(uid, status), 'req', uid)
const pollFailed = (uid: string, kind: 'network' | 'not_found' | 'expired', requestId = 'req') =>
  pollAuditStatus.rejected(null, requestId, uid, { message: kind, kind })

describe('auditSlice: статусы и опрос', () => {
  it.each<AuditWorkflowStatus>(['completed', 'partial', 'failed', 'cancelled', 'expired', 'unknown'])(
    'статус %s останавливает опрос',
    (status) => {
      const state = reducer(reducer(initial, selectAudit('a1')), polled('a1', status))
      expect(state.polling).toBe(false)
      expect(state.currentAudit?.status).toBe(status)
    },
  )

  it.each<AuditWorkflowStatus>(['queued', 'running'])('статус %s опрос не останавливает', (status) => {
    const state = reducer(reducer(initial, selectAudit('a1')), polled('a1', status))
    expect(state.polling).toBe(true)
  })

  it('ответ по другому uid не затирает открытый аудит (быстрая смена маршрута)', () => {
    let state = reducer(initial, selectAudit('a1'))
    state = reducer(state, selectAudit('a2'))
    state = reducer(state, polled('a1', 'completed'))

    expect(state.activeUid).toBe('a2')
    expect(state.currentAudit).toBeNull()
  })

  it('смена маршрута скрывает данные прежнего аудита до ответа по новому', () => {
    let state = reducer(initial, selectAudit('a1'))
    state = reducer(state, polled('a1', 'completed'))
    expect(state.currentAudit?.uid).toBe('a1')

    state = reducer(state, selectAudit('a2'))
    expect(state.currentAudit).toBeNull()
    expect(state.polling).toBe(true)
  })

  it('повторное открытие уже завершённого аудита не включает опрос', () => {
    let state = reducer(initial, selectAudit('a1'))
    state = reducer(state, polled('a1', 'completed'))
    state = reducer(state, selectAudit('a1'))
    expect(state.polling).toBe(false)
  })

  it('ошибка по другому uid и отменённый запрос не считаются ошибкой экрана', () => {
    let state = reducer(initial, selectAudit('a2'))
    state = reducer(state, pollFailed('a1', 'network'))
    expect(state.error).toBeNull()

    const aborted = pollAuditStatus.rejected(new Error('Aborted'), 'req', 'a2', undefined, undefined)
    aborted.meta.aborted = true
    state = reducer(state, aborted)
    expect(state.error).toBeNull()
  })

  it('временная ошибка сохраняет данные и опрос, постоянная останавливает его', () => {
    let state = reducer(initial, selectAudit('a1'))
    state = reducer(state, polled('a1', 'running'))
    state = reducer(state, pollFailed('a1', 'network'))
    expect(state.errorKind).toBe('network')
    expect(state.polling).toBe(true)
    expect(state.currentAudit?.uid).toBe('a1')

    state = reducer(state, polled('a1', 'running'))
    expect(state.error).toBeNull()

    state = reducer(state, pollFailed('a1', 'expired'))
    expect(state.errorKind).toBe('expired')
    expect(state.polling).toBe(false)
  })

  it('ответ предыдущего запуска не затирает новый', () => {
    let state = reducer(initial, startAudit.pending('first', { url: 'https://a.example' }))
    state = reducer(state, startAudit.pending('second', { url: 'https://b.example' }))
    state = reducer(state, startAudit.fulfilled(audit('from-first'), 'first', { url: 'https://a.example' }))
    expect(state.currentAudit).toBeNull()
    expect(state.loading).toBe(true)

    state = reducer(state, startAudit.fulfilled(audit('from-second', 'queued'), 'second', { url: 'https://b.example' }))
    expect(state.currentAudit?.uid).toBe('from-second')
    expect(state.activeUid).toBe('from-second')
    expect(state.loading).toBe(false)
  })

  it('ошибка запуска сохраняет код API и не выдаётся за «аудит не найден»', () => {
    let state = reducer(initial, startAudit.pending('r1', { url: 'https://a.example' }))
    state = reducer(
      state,
      startAudit.rejected(null, 'r1', { url: 'https://a.example' }, { message: 'Права не подтверждены', kind: 'error', code: 'domain_not_verified' }),
    )
    expect(state.error).toBe('Права не подтверждены')
    expect(state.errorKind).toBe('error')
    expect(state.errorCode).toBe('domain_not_verified')
    expect(state.loading).toBe(false)
  })

  it('отмена переводит аудит в конечный статус и останавливает опрос', () => {
    let state = reducer(initial, selectAudit('a1'))
    state = reducer(state, polled('a1', 'running'))
    state = reducer(state, cancelAuditRequest.pending('c1', 'a1'))
    expect(state.cancelling).toBe(true)
    state = reducer(state, cancelAuditRequest.fulfilled(audit('a1', 'cancelled'), 'c1', 'a1'))
    expect(state.cancelling).toBe(false)
    expect(state.currentAudit?.status).toBe('cancelled')
    expect(state.polling).toBe(false)
  })
})
