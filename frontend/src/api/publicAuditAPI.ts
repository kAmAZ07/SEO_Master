import api from './axiosConfig'
import { AuditDetail, AuditIssueCounters, AuditRequest, AuditStatus } from '@/types/audit'

type JsonObject = Record<string, unknown>

const toObject = (value: unknown): JsonObject => {
  if (typeof value === 'object' && value !== null) {
    return value as JsonObject
  }

  return {}
}

const toString = (value: unknown, fallback = ''): string => {
  if (typeof value === 'string') {
    return value
  }

  if (value === null || value === undefined) {
    return fallback
  }

  return String(value)
}

const toNumber = (value: unknown, fallback = 0): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const mapSeverityToStatus = (severity: string): AuditDetail['status'] => {
  if (severity === 'critical' || severity === 'high' || severity === 'error') {
    return 'error'
  }

  if (severity === 'warning' || severity === 'medium' || severity === 'low') {
    return 'warning'
  }

  return 'success'
}

const mapFindingsToDetails = (findingsPayload: unknown): AuditDetail[] => {
  if (!Array.isArray(findingsPayload)) {
    return []
  }

  return findingsPayload.slice(0, 30).map((finding) => {
    const item = toObject(finding)
    const code = toString(item.code || item.title || 'audit_finding')
    const severity = toString(item.severity || 'info').toLowerCase()

    return {
      title: code.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
      description: toString(item.message || item.description || code),
      status: mapSeverityToStatus(severity),
      recommendation: toString(item.recommendation || ''),
    }
  })
}

const buildIssueCounters = (details: AuditDetail[]): AuditIssueCounters => {
  const counters: AuditIssueCounters = {
    passed: 0,
    warnings: 0,
    errors: 0,
  }

  for (const detail of details) {
    if (detail.status === 'error') {
      counters.errors += 1
    } else if (detail.status === 'warning') {
      counters.warnings += 1
    } else {
      counters.passed += 1
    }
  }

  return counters
}

const computeScore = (summaryPayload: unknown, issues: AuditIssueCounters): number => {
  const summary = toObject(summaryPayload)
  const directScore = toNumber(summary.score, -1)

  if (directScore >= 0) {
    return Math.max(0, Math.min(100, Math.round(directScore)))
  }

  const penalty = issues.errors * 20 + issues.warnings * 6
  return Math.max(0, 100 - penalty)
}

const normalizeStatus = (rawStatus: string): AuditStatus['status'] => {
  const status = rawStatus.toLowerCase()

  if (status === 'queued') {
    return 'queued'
  }
  if (status === 'running') {
    return 'running'
  }
  if (status === 'pending') {
    return 'pending'
  }
  if (status === 'processing') {
    return 'processing'
  }
  if (status === 'in_progress') {
    return 'in_progress'
  }
  if (status === 'failed') {
    return 'failed'
  }

  return 'completed'
}

const normalizeAuditStatus = (payload: unknown): AuditStatus => {
  const data = toObject(payload)
  const results = toObject(data.results)
  const details = mapFindingsToDetails(results.findings)
  const issues = buildIssueCounters(details)

  return {
    uid: toString(data.uid || data.audit_id || data.id),
    id: toString(data.id || data.uid || data.audit_id),
    url: toString(data.url || data.root_url || data.target_url),
    createdAt: toString(data.created_at || data.createdAt || new Date().toISOString()),
    status: normalizeStatus(toString(data.status, 'pending')),
    progress: toNumber(data.progress, 0),
    score: computeScore(results.summary, issues),
    issues,
    details,
    result: undefined,
    error: toString(data.error || ''),
  }
}

export const submitAuditRequest = async (request: AuditRequest): Promise<AuditStatus> => {
  const response = await api.post('/public/quick-audit', request)
  return normalizeAuditStatus(response.data)
}

export const getAuditStatus = async (uid: string): Promise<AuditStatus> => {
  const response = await api.get(`/public/audit-status/${uid}`)
  return normalizeAuditStatus(response.data)
}

export const fetchAuditHistory = async (projectId?: string | number): Promise<AuditStatus[]> => {
  const response = await api.get('/audit/history', {
    params: projectId ? { projectId } : undefined,
  })

  const payload = response.data
  if (Array.isArray(payload)) {
    return payload.map(normalizeAuditStatus)
  }
  if (Array.isArray(payload?.items)) {
    return payload.items.map(normalizeAuditStatus)
  }
  if (Array.isArray(payload?.history)) {
    return payload.history.map(normalizeAuditStatus)
  }

  return []
}
