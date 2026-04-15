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

const asNumberOrUndefined = (value: unknown): number | undefined => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
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
    impactScore: asNumberOrUndefined(item.impact_score || item.impactScore),
    recommendation: asString(item.recommendation) || undefined,
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

  if (!Array.isArray(response.data)) {
    return []
  }

  return response.data.map((item: unknown) => normalizeHITLTask(item))
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
