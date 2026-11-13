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
  const version = asObject(asObject(item.diff_data || item.diffData).recommendation_version)
  const execution = asObject(item.execution)
  const recovery = asObject(execution.recovery)

  return {
    id: asString(item.id || taskId),
    taskId,
    projectId: asString(item.project_id || item.projectId),
    status: normalizeStatus(item.status),
    authorizationStatus: asString(item.authorization_status, 'invalidated'),
    recommendationVersionId: asString(version.version_id) || null,
    contentHash: asString(version.content_hash) || null,
    managedTarget: version.execution_mode === 'managed' && !!asObject(version.target).entity,
    execution: typeof execution.operation_id === 'string' ? {
      operationId: execution.operation_id, state: asString(execution.state),
      recommendationVersionId: asString(execution.recommendation_version_id), contentHash: asString(execution.content_hash),
      recovery: typeof recovery.attempts === 'number' && typeof recovery.max_attempts === 'number' ? {
        attempts: recovery.attempts, maxAttempts: recovery.max_attempts,
        lastError: asString(recovery.last_error) || null, stopReason: asString(recovery.stop_reason) || null,
      } : null,
    } : null,
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
      status_filter: 'all',
      limit: 50,
    },
  })

  const payload = response.data
  const items = Array.isArray(payload) ? payload : Array.isArray(payload?.approvals) ? payload.approvals : []
  return items.map((item: unknown) => normalizeHITLTask(item))
}

export const approveTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/approve`, {
    approval_id: approval.approvalId,
    recommendation_version_id: approval.recommendationVersionId,
    content_hash: approval.contentHash,
    comment: approval.comment,
  })
}

export const rejectTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/reject`, {
    approval_id: approval.approvalId,
    recommendation_version_id: approval.recommendationVersionId,
    content_hash: approval.contentHash,
    comment: approval.comment,
  })
}

export const fetchTaskDetails = async (taskId: string): Promise<HITLTask> => {
  const response = await api.get(`/hitl/tasks/${taskId}`)
  return normalizeHITLTask(response.data)
}

export const applyTask = async (approval: HITLApproval, operationId: string): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/apply`, {
    operation_id: operationId, task_id: approval.taskId, approval_id: approval.approvalId,
    recommendation_version_id: approval.recommendationVersionId, content_hash: approval.contentHash,
  })
}
