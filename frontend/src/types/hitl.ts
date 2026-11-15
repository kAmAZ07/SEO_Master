export interface HITLDiffData {
  before: Record<string, unknown>
  after: Record<string, unknown>
}

export interface HITLTaskDetails {
  id: string
  title: string
  description?: string
  url?: string
  taskType?: string
  status?: string
  metadata: Record<string, unknown>
}

export interface HITLVerification {
  status: string; attempts: number; maxAttempts: number; deadlineAt: string; stopReason: string | null
  observation?: { url: string; observedAt: string; status: number } | null
  checks: { field: string; matches: boolean }[]
}

export interface HITLTask {
  priority?: PrioritySnapshot | null
  id: string
  taskId: string
  projectId: string
  status: 'pending' | 'approved' | 'rejected'
  authorizationStatus?: string
  recommendationVersionId?: string | null
  contentHash?: string | null
  managedTarget?: boolean
  execution?: {
    operationId: string; state: string; recommendationVersionId: string; contentHash: string
    prepared?: boolean
    verification?: HITLVerification | null
    rollback?: { operationId: string; state: string; reason: string; verification: HITLVerification | null } | null
    preparation?: { attempts: number; maxAttempts: number; errorCode: string | null } | null
    recovery?: { attempts: number; maxAttempts: number; lastError: string | null; stopReason: string | null } | null
  } | null
  diffData: HITLDiffData
  impactScore?: number | null
  recommendation?: string
  approvedBy?: string | null
  approvedAt?: string | null
  rejectedBy?: string | null
  rejectedAt?: string | null
  rejectionReason?: string | null
  metadata: Record<string, unknown>
  createdAt?: string
  updatedAt?: string
  task?: HITLTaskDetails | null
}

export type PriorityUrgency = 'critical' | 'high' | 'medium' | 'low' | 'unknown'
export interface PriorityInputs {
  impact: number | null; confidence: number | null; effort: number | null
  urgency: PriorityUrgency; urgency_reason: string | null
}
export interface PrioritySnapshot {
  formula_version: 'priority_v1'; revision: number; inputs: PriorityInputs
  computed_score: number | null; effective_score: number | null; urgency_rank: number
  status: 'unknown' | 'estimated' | 'manual_override'; heuristic: true
  override: { score: number; actor_id: string; reason: string; at: string } | null
}
export type PriorityChange = {
  expected_revision: number; reason: string
} & ({ action: 'estimate'; inputs: PriorityInputs } | { action: 'override'; score: number } | { action: 'clear_override' })
export interface PriorityHistory {
  id: string; action: string; actor_id: string; reason: string; revision: number; at: string
  before: PrioritySnapshot; after: PrioritySnapshot
}

export interface HITLApproval {
  taskId: string
  approvalId: string
  recommendationVersionId: string
  contentHash: string
  approved: boolean
  comment?: string
}

export type HITLOperationContext = Pick<HITLTask, 'taskId' | 'projectId' | 'recommendationVersionId' | 'contentHash'>

export interface HITLCMSReceipt {
  receiptId: string
  operationId: string
  outcome: 'applied_unverified' | 'rollback_applied_unverified' | 'no_op'
  appliedAt: string
  requestHash: string
  beforeDigest: string
  afterDigest: string
  appliedFields: string[]
}

export interface HITLOperation {
  operationId: string
  state: string
  operationType: 'apply' | 'rollback'
  recordHash: string
  requestHash: string | null
  before: unknown
  after: unknown
  receipt: HITLCMSReceipt | null
  verification: HITLVerification | null
}
