import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import api from './axiosConfig'
import { cancelAudit, classifyAuditError, getAuditStatus, normalizeAudit, normalizeStatus } from './publicAuditAPI'

const publicPayload = (over: Record<string, unknown> = {}) => ({
  uid: 'a1', audit_id: 'a1', root_url: 'https://example.com', status: 'completed', progress: 100,
  results: { summary: {}, findings: [], pages: [] }, ...over,
})

describe('normalizeStatus', () => {
  it.each([
    ['queued', 'queued'], ['pending', 'queued'], ['running', 'running'], ['processing', 'running'],
    ['in_progress', 'running'], ['completed', 'completed'], ['partial', 'partial'], ['failed', 'failed'],
    ['cancelled', 'cancelled'], ['expired', 'expired'], ['COMPLETED', 'completed'],
  ])('%s → %s', (raw, expected) => {
    expect(normalizeStatus(raw)).toBe(expected)
  })

  it('незнакомый статус не выдаётся за «выполняется»', () => {
    expect(normalizeStatus('exploded')).toBe('unknown')
    expect(normalizeStatus('')).toBe('unknown')
  })
})

describe('normalizeAudit', () => {
  it('не выдумывает оценку: без summary.score она null', () => {
    const result = normalizeAudit(publicPayload({
      results: { summary: {}, top_findings: [{ code: 'x', severity: 'critical', title: 't', description: 'd' }] },
    }))
    expect(result.score).toBeNull()
    expect(result.issues.errors).toBe(1)
  })

  it('берёт оценку из ответа сервера и ограничивает диапазоном', () => {
    expect(normalizeAudit(publicPayload({ results: { summary: { score: 73.4 } } })).score).toBe(73)
    expect(normalizeAudit(publicPayload({ results: { summary: { score: 140 } } })).score).toBe(100)
    expect(normalizeAudit(publicPayload({ results: { summary: { score: 'n/a' } } })).score).toBeNull()
    expect(normalizeAudit(publicPayload({ results: { summary: { score: 0 } } })).score).toBe(0)
  })

  it('передаёт причины частичного результата и код ошибки', () => {
    const result = normalizeAudit(publicPayload({
      status: 'partial', results: { summary: {}, partial_reasons: ['deadline', 'robots_blocked'], error_code: 'psi_failed' },
    }))
    expect(result.status).toBe('partial')
    expect(result.partialReasons).toEqual(['deadline', 'robots_blocked'])
    expect(result.errorCode).toBe('psi_failed')
  })

  it('ответ без статуса получает unknown, а не running', () => {
    expect(normalizeAudit({ uid: 'a1' }).status).toBe('unknown')
  })
})

describe('classifyAuditError', () => {
  const error = (status?: number, code = 'ERR') =>
    new AxiosError('x', code, undefined, null, status === undefined ? undefined : {
      status, statusText: '', headers: {}, data: {}, config: {} as InternalAxiosRequestConfig,
    })

  it.each([
    [error(404), 'not_found'], [error(410), 'expired'], [error(429), 'rate_limited'], [error(503), 'unavailable'],
    [error(400), 'error'], [error(undefined), 'network'], [error(undefined, 'ECONNABORTED'), 'timeout'],
    [new Error('boom'), 'error'],
  ])('классифицирует ошибку', (input, expected) => {
    expect(classifyAuditError(input)).toBe(expected)
  })
})

describe('getAuditStatus', () => {
  const originalAdapter = api.defaults.adapter
  const respond = (config: InternalAxiosRequestConfig, status: number, data: unknown = {}) => {
    const response = { data, status, statusText: '', headers: {}, config }
    return status >= 400
      ? Promise.reject(new AxiosError('x', 'ERR', config, null, response))
      : Promise.resolve(response)
  }

  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    api.defaults.adapter = originalAdapter
  })

  it.each([401, 403, 404])('приватный запрос %i не скрывает публичный отчёт', async (status) => {
    localStorage.setItem('token', 'expired')
    const urls: string[] = []
    api.defaults.adapter = (config) => {
      urls.push(String(config.url))
      return config.url?.startsWith('/audit/status') ? respond(config, status) : respond(config, 200, publicPayload())
    }

    const result = await getAuditStatus('a1')

    expect(result.status).toBe('completed')
    expect(urls).toEqual(['/audit/status/a1', '/public/audit-status/a1'])
  })

  it('ошибка сервера на приватном запросе не маскируется публичным ответом', async () => {
    localStorage.setItem('token', 'valid')
    api.defaults.adapter = (config) => respond(config, 503)
    await expect(getAuditStatus('a1')).rejects.toMatchObject({ response: { status: 503 } })
  })

  it('без токена запрашивает только публичный статус', async () => {
    const urls: string[] = []
    api.defaults.adapter = (config) => {
      urls.push(String(config.url))
      return respond(config, 200, publicPayload())
    }
    await getAuditStatus('a1')
    expect(urls).toEqual(['/public/audit-status/a1'])
  })
})

describe('cancelAudit', () => {
  it('отправляет POST на публичный маршрут отмены', async () => {
    const original = api.defaults.adapter
    const calls: string[] = []
    api.defaults.adapter = (config) => {
      calls.push(`${config.method} ${config.url}`)
      return Promise.resolve({ data: publicPayload({ status: 'cancelled' }), status: 200, statusText: '', headers: {}, config })
    }
    const result = await cancelAudit('a1')
    api.defaults.adapter = original

    expect(calls).toEqual(['post /public/audit/a1/cancel'])
    expect(result.status).toBe('cancelled')
    vi.restoreAllMocks()
  })
})
