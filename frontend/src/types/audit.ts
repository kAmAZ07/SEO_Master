export interface AuditRequest {
  url: string
  projectId?: number
}

export type AuditWorkflowStatus = 'queued' | 'pending' | 'processing' | 'in_progress' | 'completed' | 'failed'
export type AuditFindingStatus = 'success' | 'warning' | 'error' | 'info'

export interface AuditIssueSummary {
  passed: number
  warnings: number
  errors: number
}

export interface AuditCoverage {
  attempted: number
  processed: number
  max_pages: number
}

export interface AuditPage {
  url: string
  final_url?: string | null
  status_code?: number | null
  title?: string | null
  description?: string | null
  h1?: string | null
  links?: string[]
  error?: string | null
}

export interface AuditFinding {
  code: string
  title: string
  description: string
  recommendation?: string
  category?: string
  severity?: string
  confidence?: string
  status: AuditFindingStatus
}

export interface AuditScoreBreakdown {
  base_score: number
  penalty_points: number
  coverage_bonus: number
  crawl_bonus: number
  crawl_completion_ratio: number
}

export interface AuditSummary {
  score?: number
  score_explanation?: string
  score_breakdown?: AuditScoreBreakdown
  issue_counts?: Record<string, number>
  coverage?: AuditCoverage
  cwv?: Record<string, string | number | null>
  links_checked?: number
  [key: string]: unknown
}

export interface AuditStatus {
  uid: string
  id: string
  url: string
  status: AuditWorkflowStatus
  progress: number
  createdAt: string
  score: number
  issues: AuditIssueSummary
  details: AuditFinding[]
  summary?: AuditSummary
  pages?: AuditPage[]
  error?: string
}
