import api from './axiosConfig'
import { fetchDashboardStats } from './dashboardAPI'
import { fetchHITLTasks } from './hitlAPI'
import type { Project } from '@/types/dashboard'
import type { AuditHistoryItem, ManagementTask, NotificationItem } from '@/types/notifications'

const asObject = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}

const asString = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback

const asNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const normalizeTask = (value: unknown): ManagementTask => {
  const item = asObject(value)
  return {
    id: asString(item.id),
    projectId: asString(item.project_id || item.projectId),
    taskType: asString(item.task_type || item.taskType, 'SEO_TASK'),
    status: asString(item.status, 'pending').toLowerCase(),
    url: asString(item.url) || null,
    title: asString(item.title) || null,
    description: asString(item.description) || null,
    impactScore: asNumber(item.impact_score || item.impactScore),
    priorityScore: asNumber(item.priority_score || item.priorityScore),
    createdAt: asString(item.created_at || item.createdAt) || null,
    updatedAt: asString(item.updated_at || item.updatedAt) || null,
    completedAt: asString(item.completed_at || item.completedAt) || null,
    deployedAt: asString(item.deployed_at || item.deployedAt) || null,
  }
}

const normalizeAudit = (value: unknown): AuditHistoryItem => {
  const item = asObject(value)
  const uid = asString(item.uid || item.audit_id || item.id)
  return {
    id: asString(item.id || uid),
    uid,
    projectId: asString(item.projectId || item.project_id) || null,
    url: asString(item.url || item.root_url),
    mode: asString(item.mode) || null,
    status: asString(item.status, 'unknown').toLowerCase(),
    score: asNumber(item.score),
    createdAt: asString(item.createdAt || item.created_at) || null,
    updatedAt: asString(item.updatedAt || item.updated_at) || null,
  }
}

const projectHref = (projectId?: string | null) => projectId ? `/dashboard/projects/${projectId}` : '/dashboard/projects'

const buildProjectMap = (projects: Project[]): Map<string, Project> =>
  new Map(projects.map((project) => [project.id, project]))

const taskTitle = (task: ManagementTask): string => {
  if (task.title) return task.title
  if (task.url) return task.url
  return task.taskType.split('_').join(' ')
}

const buildHITLNotifications = async (): Promise<NotificationItem[]> => {
  const tasks = await fetchHITLTasks()
  return tasks
    .filter((task) => task.status === 'pending')
    .map((task) => ({
      id: `hitl:${task.taskId}`,
      type: 'hitl_pending',
      category: 'action',
      severity: (task.impactScore ?? 0) >= 70 ? 'critical' : 'warning',
      title: 'Требуется HITL-согласование',
      description: task.task?.title || task.recommendation || 'Проверьте предложенное SEO-изменение перед внедрением.',
      href: '/dashboard/hitl',
      actionLabel: 'Согласовать',
      requiresAction: true,
      createdAt: task.createdAt || task.updatedAt || null,
      projectId: task.projectId,
    }) satisfies NotificationItem)
}

const buildAuditNotifications = async (): Promise<NotificationItem[]> => {
  const response = await api.get('/audit/history')
  const items = Array.isArray(response.data) ? response.data.map(normalizeAudit) : []

  return items.slice(0, 12).map((audit) => {
    const isFailed = audit.status === 'failed'
    const isActive = ['queued', 'running', 'pending', 'processing', 'in_progress'].includes(audit.status)
    const href = audit.projectId
      ? `/dashboard/projects/${audit.projectId}/audits/${audit.uid}`
      : `/audit/results/${audit.uid}`

    return {
      id: `audit:${audit.uid}:${audit.status}`,
      type: isFailed ? 'audit_failed' : isActive ? 'audit_running' : 'audit_completed',
      category: 'audit',
      severity: isFailed ? 'critical' : isActive ? 'info' : 'success',
      title: isFailed ? 'Аудит не выполнен' : isActive ? 'Аудит выполняется' : 'Аудит готов',
      description: `${audit.url}${audit.score != null && !isActive && !isFailed ? ` - оценка ${audit.score}/100` : ''}`,
      href: isFailed && audit.projectId ? `${projectHref(audit.projectId)}?tab=audit` : href,
      actionLabel: isActive ? 'Открыть проект' : 'Открыть отчет',
      requiresAction: isFailed,
      createdAt: audit.updatedAt || audit.createdAt,
      projectId: audit.projectId,
    } satisfies NotificationItem
  })
}

const buildTaskNotifications = async (): Promise<NotificationItem[]> => {
  const response = await api.get('/tasks', { params: { limit: 50 } })
  const tasks = Array.isArray(response.data) ? response.data.map(normalizeTask) : []

  return tasks.slice(0, 15).map((task) => {
    const isCompleted = task.status === 'completed'
    const isFailed = task.status === 'failed'
    const isDeployed = Boolean(task.deployedAt)

    return {
      id: `task:${task.id}:${task.status}:${task.deployedAt || task.updatedAt || task.createdAt || ''}`,
      type: isDeployed ? 'task_deployed' : isCompleted ? 'task_completed' : isFailed ? 'task_failed' : 'task_created',
      category: 'task',
      severity: isFailed ? 'critical' : isCompleted || isDeployed ? 'success' : 'info',
      title: isDeployed ? 'Изменение внедрено' : isCompleted ? 'Задача выполнена' : isFailed ? 'Задача не выполнена' : 'Новая SEO-задача',
      description: task.description || taskTitle(task),
      href: projectHref(task.projectId),
      actionLabel: 'Открыть проект',
      requiresAction: isFailed,
      createdAt: task.deployedAt || task.completedAt || task.updatedAt || task.createdAt,
      projectId: task.projectId,
    } satisfies NotificationItem
  })
}

const buildScoreNotifications = (projects: Project[]): NotificationItem[] =>
  projects
    .filter((project) => project.ffScore?.timestamp)
    .slice(0, 8)
    .map((project) => ({
      id: `ffscore:${project.id}:${project.ffScore?.timestamp}`,
      type: 'ffscore_updated',
      category: 'score',
      severity: 'info',
      title: 'FF-Score обновлен',
      description: `${project.name}: ${Math.round(project.ffScore?.total ?? 0)}/100`,
      href: projectHref(project.id),
      actionLabel: 'Открыть проект',
      requiresAction: false,
      createdAt: project.ffScore?.timestamp,
      projectId: project.id,
      projectName: project.name,
    }))

export const fetchNotificationItems = async (): Promise<NotificationItem[]> => {
  const [hitlResult, auditResult, taskResult, statsResult] = await Promise.allSettled([
    buildHITLNotifications(),
    buildAuditNotifications(),
    buildTaskNotifications(),
    fetchDashboardStats(),
  ])

  const stats = statsResult.status === 'fulfilled' ? statsResult.value : null
  const projectMap = buildProjectMap(stats?.recentProjects ?? [])
  const items = [
    ...(hitlResult.status === 'fulfilled' ? hitlResult.value : []),
    ...(auditResult.status === 'fulfilled' ? auditResult.value : []),
    ...(taskResult.status === 'fulfilled' ? taskResult.value : []),
    ...(stats ? buildScoreNotifications(stats.recentProjects) : []),
  ].map((item) => ({
    ...item,
    projectName: item.projectName || (item.projectId ? projectMap.get(item.projectId)?.name : null) || null,
  }))

  if (!items.length && [hitlResult, auditResult, taskResult, statsResult].every((result) => result.status === 'rejected')) {
    throw new Error('notifications_unavailable')
  }

  return items
    .filter((item, index, array) => array.findIndex((candidate) => candidate.id === item.id) === index)
    .sort((left, right) => {
      const leftTime = left.createdAt ? new Date(left.createdAt).getTime() : 0
      const rightTime = right.createdAt ? new Date(right.createdAt).getTime() : 0
      return rightTime - leftTime
    })
    .slice(0, 40)
}
