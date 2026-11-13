import axios from 'axios'

export interface ParsedApiError {
  status?: number
  // Стабильный код ошибки API: detail.error (ошибки проектов) или error.code (обёртка шлюза).
  code?: string
  reason?: string
  message?: string
  // Секунды из заголовка Retry-After (429/503).
  retryAfter?: number
}

const asRecord = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}

const asText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value : undefined

export const parseApiError = (error: unknown): ParsedApiError => {
  if (!axios.isAxiosError(error)) {
    return {}
  }

  const data = asRecord(error.response?.data)
  const detail = data.detail
  const detailRecord = asRecord(detail)
  const wrapped = asRecord(data.error)

  const retryAfterRaw = Number(error.response?.headers?.['retry-after'])

  return {
    status: error.response?.status,
    code: asText(detailRecord.error) ?? asText(wrapped.code) ?? (typeof detail === 'string' ? detail : undefined),
    reason: asText(detailRecord.reason),
    message: asText(detailRecord.message) ?? asText(wrapped.message) ?? asText(detail),
    retryAfter: Number.isFinite(retryAfterRaw) && retryAfterRaw > 0 ? retryAfterRaw : undefined,
  }
}
