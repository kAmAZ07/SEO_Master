import api from './axiosConfig'
import type { HITLApproval, HITLDiffData, HITLTask } from '@/types/hitl'

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

const asNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const normalizeDiffData = (value: unknown): HITLDiffData => {
  const diff = asObject(value)
  return {
    before: asObject(diff.before),
    after: asObject(diff.after),
  }
}

const normalizeTask = (payload: unknown): HITLTask => {
  const data = asObject(payload)
  const status = asString(data.status, 'pending').toLowerCase()

  return {
    id: asString(data.id),
    taskId: asString(data.task_id || data.taskId),
    projectId: asString(data.project_id || data.projectId),
    status: status === 'approved' || status === 'rejected' ? status : 'pending',
    diffData: normalizeDiffData(data.diff_data || data.diffData),
    impactScore: asNumber(data.impact_score ?? data.impactScore),
    recommendation: asString(data.recommendation) || null,
    approvedBy: asString(data.approved_by || data.approvedBy) || null,
    approvedAt: asString(data.approved_at || data.approvedAt) || null,
    rejectedBy: asString(data.rejected_by || data.rejectedBy) || null,
    rejectedAt: asString(data.rejected_at || data.rejectedAt) || null,
    rejectionReason: asString(data.rejection_reason || data.rejectionReason) || null,
    metadata: asObject(data.metadata),
    createdAt: asString(data.created_at || data.createdAt || new Date().toISOString()),
    updatedAt: asString(data.updated_at || data.updatedAt || data.created_at || data.createdAt || new Date().toISOString()),
  }
}

export const fetchHITLTasks = async (): Promise<HITLTask[]> => {
  const response = await api.get('/hitl/tasks')
  const payload = response.data
  const items = Array.isArray(payload) ? payload : Array.isArray(payload?.approvals) ? payload.approvals : []
  return items.map((item: unknown) => normalizeTask(item))
}

export const approveTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/approve`, {
    comment: approval.comment,
  })
}

export const rejectTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/reject`, {
    comment: approval.comment,
  })
}

export const fetchTaskDetails = async (taskId: string): Promise<HITLTask> => {
  const response = await api.get(`/hitl/tasks/${taskId}`)
  return normalizeTask(response.data)
}
