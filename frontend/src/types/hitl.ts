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
