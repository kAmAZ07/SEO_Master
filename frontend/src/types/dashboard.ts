export type FFScoreStatus = 'complete' | 'insufficient_data' | 'legacy_estimated'

export interface FFScorePartial {
  score?: number | null
  coverage?: number | null
  comparable_to_full_score?: boolean
  [key: string]: unknown
}

// Полный балл и компоненты бывают null: недоступное значение не заменяется нулём.
export interface FFScore {
  total: number | null
  status: FFScoreStatus
  formulaVersion: string
  heuristic: boolean
  legacyEstimated: boolean
  freshness: number | null
  familiarity: number | null
  quality: number | null
  partial?: FFScorePartial | null
  coverage?: Record<string, unknown> | null
  timestamp: string
}

export interface ProjectStats {
  audits: number
  keywords: number
  pages: number
  backlinks: number
}

export type VerificationState = 'unverified' | 'pending' | 'verified' | 'stale'

export interface ProjectScope {
  origin: string | null
  pathPrefix: string | null
}

// Сводка подтверждения владения сайтом: contracts/projects.md, раздел «Подтверждение: HTTP-challenge».
export interface VerificationSummary {
  state: VerificationState
  scope?: ProjectScope | null
  freshDays?: number | null
  verifiedAt?: string | null
  freshUntil?: string | null
  challengeExpiresAt?: string | null
  attempts?: number | null
  lastErrorCode?: string | null
  lastStatus?: string | null
}

// Результат присоединения публичного аудита при создании проекта.
export interface ProjectClaim {
  status: 'claimed' | 'rejected'
  reason?: 'invalid_claim' | 'already_claimed' | 'out_of_scope' | string
}

export interface Project {
  id: string
  name: string
  url: string
  description?: string | null
  scope?: ProjectScope | null
  verification?: VerificationSummary | null
  claim?: ProjectClaim | null
  ffScore?: FFScore
  tasks?: number
  lastAudit?: string | null
  // scope_review — у проекта нет корректной области сайта: он читается, но не допускается к аудитам по области.
  status: 'active' | 'paused' | 'scope_review' | string
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
  avgFFScore: number | null
  ffScoreProjects: number
  recentProjects: Project[]
  recentAudits: DashboardAuditSummary[]
}
