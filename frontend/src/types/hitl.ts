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

export interface HITLTask {
  id: string
  taskId: string
  projectId: string
  status: 'pending' | 'approved' | 'rejected'
  authorizationStatus?: string
  recommendationVersionId?: string | null
  contentHash?: string | null
  managedTarget?: boolean
  execution?: { operationId: string; state: string; recommendationVersionId: string; contentHash: string } | null
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
