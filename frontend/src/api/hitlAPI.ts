import api from './axiosConfig'
import type { HITLApproval, HITLDiffData, HITLTask, HITLTaskDetails } from '@/types/hitl'

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

const asNullableNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const normalizeStatus = (value: unknown): HITLTask['status'] => {
  const status = asString(value, 'pending').toLowerCase()
  if (status === 'approved' || status === 'rejected') {
    return status
  }
  return 'pending'
}

const normalizeTaskDetails = (value: unknown): HITLTaskDetails | null => {
  const task = asObject(value)
  const id = asString(task.id)

  if (!id) {
    return null
  }

  return {
    id,
    title: asString(task.title, 'HITL task'),
    description: asString(task.description) || undefined,
    url: asString(task.url) || undefined,
    taskType: asString(task.task_type || task.taskType) || undefined,
    status: asString(task.status) || undefined,
    metadata: asObject(task.metadata),
  }
}

const normalizeDiffData = (value: unknown): HITLDiffData => {
  const diff = asObject(value)
  return {
    before: asObject(diff.before),
    after: asObject(diff.after),
  }
}

const normalizeHITLTask = (value: unknown): HITLTask => {
  const item = asObject(value)
  const task = normalizeTaskDetails(item.task)
  const taskId = asString(item.task_id || item.taskId || task?.id || item.id)

  return {
    id: asString(item.id || taskId),
    taskId,
    projectId: asString(item.project_id || item.projectId),
    status: normalizeStatus(item.status),
    diffData: normalizeDiffData(item.diff_data || item.diffData),
    impactScore: asNullableNumber(item.impact_score ?? item.impactScore),
    recommendation: asString(item.recommendation) || undefined,
    approvedBy: asString(item.approved_by || item.approvedBy) || null,
    approvedAt: asString(item.approved_at || item.approvedAt) || null,
    rejectedBy: asString(item.rejected_by || item.rejectedBy) || null,
    rejectedAt: asString(item.rejected_at || item.rejectedAt) || null,
    rejectionReason: asString(item.rejection_reason || item.rejectionReason) || null,
    metadata: asObject(item.metadata),
    createdAt: asString(item.created_at || item.createdAt) || undefined,
    updatedAt: asString(item.updated_at || item.updatedAt) || undefined,
    task,
  }
}

export const fetchHITLTasks = async (): Promise<HITLTask[]> => {
  const response = await api.get('/hitl/tasks', {
    params: {
      status_filter: 'pending',
      limit: 50,
    },
  })

  const payload = response.data
  const items = Array.isArray(payload) ? payload : Array.isArray(payload?.approvals) ? payload.approvals : []
  return items.map((item: unknown) => normalizeHITLTask(item))
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
  return normalizeHITLTask(response.data)
}
