import api from './axiosConfig'
import axios from 'axios'
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

const normalizeStatus = (status: string): AuditStatus['status'] => {
  const value = status.toLowerCase()
  if (value === 'queued' || value === 'pending' || value === 'processing' || value === 'in_progress' || value === 'completed' || value === 'failed') {
    return value
  }
  return 'processing'
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

const calculateScore = (summary: AuditSummary, issues: AuditIssueSummary): number => {
  const directScore = asNumber(summary.score, -1)
  if (directScore >= 0) {
    return Math.max(0, Math.min(100, Math.round(directScore)))
  }

  const fallback = 100 - issues.errors * 18 - issues.warnings * 5
  return Math.max(0, Math.min(100, Math.round(fallback)))
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

const normalizeAudit = (payload: unknown): AuditStatus => {
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
    status: normalizeStatus(asString(data.status, 'processing')),
    progress: asNumber(data.progress, 0),
    score: calculateScore(summary, issues),
    issues,
    details,
    summary,
    pages,
    error: asString(data.error),
  }
}

export const submitAuditRequest = async (request: AuditRequest): Promise<AuditStatus> => {
  const token = localStorage.getItem('token')
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
      score: 0,
      issues: { passed: 0, warnings: 0, errors: 0 },
      details: [],
      summary: undefined,
      pages: [],
      error: '',
    }
  }

  const response = await api.post('/public/quick-audit', { url: request.url })
  const data = asObject(response.data)

  return {
    uid: asString(data.uid || data.audit_id),
    id: asString(data.audit_id || data.uid || data.id),
    url: request.url,
    status: normalizeStatus(asString(data.status, 'processing')),
    progress: 10,
    createdAt: new Date().toISOString(),
    score: 0,
    issues: { passed: 0, warnings: 0, errors: 0 },
    details: [],
    summary: undefined,
    pages: [],
    error: '',
  }
}

export const getAuditStatus = async (uid: string): Promise<AuditStatus> => {
  const token = localStorage.getItem('token')
  if (token) {
    try {
      const response = await api.get(`/audit/status/${uid}`)
      return normalizeAudit(response.data)
    } catch (error) {
      if (!axios.isAxiosError(error) || ![403, 404].includes(error.response?.status ?? 0)) {
        throw error
      }
    }
  }

  const response = await api.get(`/public/audit-status/${uid}`)
  return normalizeAudit(response.data)
}
