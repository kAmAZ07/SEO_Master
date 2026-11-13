import api from './axiosConfig'
import axios from 'axios'
import { storeAuditClaimToken } from './auditClaim'
import { getAccessToken } from './session'
import type { AuditLoadErrorKind } from '@/utils/auditStatus'
import type { AuditRequest, AuditStatus, AuditFinding, AuditIssueSummary, AuditSummary, AuditPage, AuditCriterion } from '@/types/audit'

const asObject = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}

const asString = (value: unknown, fallback = ''): string => {
  if (typeof value === 'string') {
    return value
  }
  if (value === null || value === undefined) {
    return fallback
  }
  return String(value)
}

const asNumber = (value: unknown, fallback = 0): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const normalizeFindingStatus = (severity: string): AuditFinding['status'] => {
  if (severity === 'critical' || severity === 'high' || severity === 'error') {
    return 'error'
  }
  if (severity === 'warning' || severity === 'medium' || severity === 'low') {
    return 'warning'
  }
  if (severity === 'info') {
    return 'info'
  }
  return 'success'
}

// Незнакомый статус не превращается в «выполняется»: иначе опрос и индикатор загрузки не закончатся никогда.
// Старые названия (pending/processing/in_progress) приводятся к текущим статусам жизненного цикла.
export const normalizeStatus = (status: string): AuditStatus['status'] => {
  const value = status.toLowerCase()
  if (value === 'queued' || value === 'pending') {
    return 'queued'
  }
  if (value === 'running' || value === 'processing' || value === 'in_progress') {
    return 'running'
  }
  if (value === 'completed' || value === 'partial' || value === 'failed' || value === 'cancelled' || value === 'expired') {
    return value
  }
  return 'unknown'
}

const mapFindings = (findings: unknown): AuditFinding[] => {
  if (!Array.isArray(findings)) {
    return []
  }

  return findings.slice(0, 100).map((item) => {
    const finding = asObject(item)
    const severity = asString(finding.severity, 'info').toLowerCase()
    const code = asString(finding.code || finding.title || 'audit_finding')
    const title = asString(finding.title, code.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()))
    const description = asString(finding.description || finding.message, title)
    const recommendation = asString(finding.recommendation)

    return {
      code,
      title,
      description,
      recommendation: recommendation || undefined,
      category: asString(finding.category) || undefined,
      severity,
      confidence: asString(finding.confidence) || undefined,
      details: asObject(finding.details),
      status: normalizeFindingStatus(severity),
    }
  })
}

const mapCriteria = (criteria: unknown): AuditCriterion[] => {
  if (!Array.isArray(criteria)) {
    return []
  }

  return criteria.map((item) => {
    const criterion = asObject(item)
    return {
      key: asString(criterion.key, 'criterion'),
      title: asString(criterion.title, 'Audit criterion'),
      description: asString(criterion.description),
      score: Math.max(0, Math.min(100, Math.round(asNumber(criterion.score, 0)))),
      status: asString(criterion.status, 'info'),
      weight: asNumber(criterion.weight, 0),
      checked: Array.isArray(criterion.checked) ? criterion.checked.map((value) => asString(value)).filter(Boolean) : [],
      issue_count: asNumber(criterion.issue_count, 0),
      info_count: asNumber(criterion.info_count, 0),
      findings: mapFindings(criterion.findings),
    }
  })
}

const summarizeIssues = (details: AuditFinding[]): AuditIssueSummary => {
  const summary = { passed: 0, warnings: 0, errors: 0 }

  for (const detail of details) {
    if (detail.status === 'error') {
      summary.errors += 1
    } else if (detail.status === 'warning') {
      summary.warnings += 1
    } else {
      summary.passed += 1
    }
  }

  return summary
}

// Оценка берётся только из ответа сервера. Если её нет, возвращаем null: подставлять формулу на клиенте нельзя.
const readScore = (summary: AuditSummary): number | null => {
  const direct = summary.score
  if (typeof direct !== 'number' || !Number.isFinite(direct)) {
    return null
  }
  return Math.max(0, Math.min(100, Math.round(direct)))
}

const mapPages = (pages: unknown): AuditPage[] => {
  if (!Array.isArray(pages)) {
    return []
  }

  return pages.map((item) => {
    const page = asObject(item)
    return {
      url: asString(page.url),
      final_url: asString(page.final_url) || undefined,
      status_code: asNumber(page.status_code, NaN),
      title: asString(page.title) || undefined,
      description: asString(page.description) || undefined,
      h1: asString(page.h1) || undefined,
      links: Array.isArray(page.links) ? page.links.map((link) => asString(link)) : [],
      error: asString(page.error) || undefined,
    }
  })
}

export const normalizeAudit = (payload: unknown): AuditStatus => {
  const data = asObject(payload)
  const results = asObject(data.results)
  const rawSummary = Object.keys(results).length > 0 ? results.summary : data.summary
  const summary = asObject(rawSummary) as AuditSummary
  summary.criteria = mapCriteria(summary.criteria)
  const details = mapFindings(results.top_findings || results.findings || data.top_findings || data.findings)
  const criterionFindings = summary.criteria.flatMap((criterion) => criterion.findings)
  const issues = summarizeIssues(criterionFindings.length > 0 ? criterionFindings : details)
  const pages = mapPages(results.pages || data.pages)
  const uid = asString(data.uid || data.audit_id || data.id)
  const url = asString(data.url || data.root_url || data.target_url || results.root_url)

  return {
    uid,
    id: asString(data.id || uid),
    url,
    createdAt: asString(data.created_at || data.createdAt || new Date().toISOString()),
    status: normalizeStatus(asString(data.status, 'unknown')),
    progress: asNumber(data.progress, 0),
    score: readScore(summary),
    issues,
    details,
    summary,
    pages,
    error: asString(data.error),
    errorCode: asString(results.error_code) || undefined,
    partialReasons: Array.isArray(results.partial_reasons)
      ? results.partial_reasons.map((reason) => asString(reason)).filter(Boolean)
      : undefined,
  }
}

export const submitAuditRequest = async (request: AuditRequest): Promise<AuditStatus> => {
  const token = getAccessToken()
  if (request.projectId && token) {
    const response = await api.post('/audit/full', {
      projectId: request.projectId,
      url: request.url,
    })
    const data = asObject(response.data)

    return {
      uid: asString(data.uid || data.audit_id),
      id: asString(data.audit_id || data.uid || data.id),
      url: asString(data.url, request.url),
      status: normalizeStatus(asString(data.status, 'queued')),
      progress: 10,
      createdAt: new Date().toISOString(),
      score: null,
      issues: { passed: 0, warnings: 0, errors: 0 },
      details: [],
      summary: undefined,
      pages: [],
      error: '',
    }
  }

  const response = await api.post('/public/quick-audit', { url: request.url })
  const data = asObject(response.data)
  storeAuditClaimToken(asString(data.uid || data.audit_id), asString(data.claim_token))

  return {
    uid: asString(data.uid || data.audit_id),
    id: asString(data.audit_id || data.uid || data.id),
    url: request.url,
    status: normalizeStatus(asString(data.status, 'queued')),
    progress: 10,
    createdAt: new Date().toISOString(),
    score: null,
    issues: { passed: 0, warnings: 0, errors: 0 },
    details: [],
    summary: undefined,
    pages: [],
    error: '',
  }
}

export const classifyAuditError = (error: unknown): AuditLoadErrorKind => {
  if (!axios.isAxiosError(error)) {
    return 'error'
  }
  if (axios.isCancel(error)) {
    return 'aborted'
  }
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return 'timeout'
  }
  const status = error.response?.status
  if (status === undefined) {
    return 'network'
  }
  if (status === 404) {
    return 'not_found'
  }
  if (status === 410) {
    return 'expired'
  }
  if (status === 429) {
    return 'rate_limited'
  }
  if (status >= 500) {
    return 'unavailable'
  }
  return 'error'
}

// Приватный статус доступен только владельцу. Если он не подошёл (нет доступа, нет аудита, сессия
// истекла), публичный отчёт по uid остаётся доступным: ошибка приватного запроса его не скрывает.
const PRIVATE_FALLBACK_STATUSES = [401, 403, 404]

export const getAuditStatus = async (uid: string, signal?: AbortSignal): Promise<AuditStatus> => {
  if (getAccessToken()) {
    try {
      const response = await api.get(`/audit/status/${uid}`, { signal })
      return normalizeAudit(response.data)
    } catch (error) {
      if (!axios.isAxiosError(error) || !PRIVATE_FALLBACK_STATUSES.includes(error.response?.status ?? 0)) {
        throw error
      }
    }
  }

  const response = await api.get(`/public/audit-status/${uid}`, { signal })
  return normalizeAudit(response.data)
}

export const cancelAudit = async (uid: string): Promise<AuditStatus> => {
  const response = await api.post(`/public/audit/${uid}/cancel`)
  return normalizeAudit(response.data)
}
