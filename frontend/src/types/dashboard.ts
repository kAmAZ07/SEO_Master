export interface FFScore {
  total: number
  freshness: number
  familiarity: number
  quality: number
  timestamp: string
}

export interface ProjectStats {
  audits: number
  keywords: number
  pages: number
  backlinks: number
}

export interface Project {
  id: string
  name: string
  url: string
  description?: string | null
  ffScore?: FFScore
  tasks?: number
  lastAudit?: string | null
  status: 'active' | 'paused' | string
  createdAt?: string | null
  updatedAt?: string | null
  stats?: ProjectStats
}

export interface DashboardAuditSummary {
  id: string
  url: string
  score: number
  status: string
}

export interface DashboardStats {
  totalProjects: number
  activeAudits: number
  totalKeywords: number
  totalBacklinks: number
  pendingTasks: number
  completedTasks: number
  avgFFScore: number
  recentProjects: Project[]
  recentAudits: DashboardAuditSummary[]
}
