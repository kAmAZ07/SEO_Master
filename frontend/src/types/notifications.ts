export type NotificationCategory = 'action' | 'audit' | 'task' | 'score' | 'integration'

export type NotificationSeverity = 'info' | 'success' | 'warning' | 'critical'

export interface NotificationItem {
  id: string
  type: string
  category: NotificationCategory
  severity: NotificationSeverity
  title: string
  description: string
  href: string
  actionLabel: string
  requiresAction: boolean
  createdAt?: string | null
  projectId?: string | null
  projectName?: string | null
}

export interface ManagementTask {
  id: string
  projectId: string
  taskType: string
  status: string
  url?: string | null
  title?: string | null
  description?: string | null
  impactScore?: number | null
  priorityScore?: number | null
  createdAt?: string | null
  updatedAt?: string | null
  completedAt?: string | null
  deployedAt?: string | null
}

export interface AuditHistoryItem {
  id: string
  uid: string
  projectId?: string | null
  url: string
  mode?: string | null
  status: string
  score?: number | null
  createdAt?: string | null
  updatedAt?: string | null
}
