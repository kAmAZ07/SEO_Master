import api from './axiosConfig'
import type { HITLApproval, HITLDiffData, HITLTask, HITLTaskDetails, HITLVerification, HITLOperation, HITLOperationContext, HITLCMSReceipt } from '@/types/hitl'

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

const normalizeVerification = (value: unknown): HITLVerification | null => {
  const item = asObject(value)
  if (typeof item.attempts !== 'number' || typeof item.max_attempts !== 'number') return null
  const result = asObject(item.last_result)
  const observation = asObject(result.observation)
  return {
    status: asString(item.status), attempts: item.attempts, maxAttempts: item.max_attempts,
    deadlineAt: asString(item.deadline_at), stopReason: asString(item.stop_reason) || null,
    observation: typeof observation.status === 'number' ? {
      url: asString(observation.url), observedAt: asString(observation.observed_at), status: observation.status,
    } : null,
    checks: Array.isArray(result.checks) ? result.checks.map((value: unknown) => {
      const check = asObject(value)
      return { field: asString(check.field), matches: check.matches === true }
    }) : [],
  }
}

const normalizeHITLTask = (value: unknown): HITLTask => {
  const item = asObject(value)
  const task = normalizeTaskDetails(item.task)
  const taskId = asString(item.task_id || item.taskId || task?.id || item.id)
  const version = asObject(asObject(item.diff_data || item.diffData).recommendation_version)
  const execution = asObject(item.execution)
  const recovery = asObject(execution.recovery)
  const preparation = asObject(execution.preparation)
  const rollback = asObject(execution.rollback)

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
      prepared: typeof execution.prepared === 'boolean' ? execution.prepared : undefined,
      verification: normalizeVerification(execution.verification),
      rollback: typeof rollback.operation_id === 'string' ? { operationId: rollback.operation_id,
        state: asString(rollback.state), reason: asString(rollback.reason), verification: normalizeVerification(rollback.verification) } : null,
      preparation: typeof preparation.attempts === 'number' && typeof preparation.max_attempts === 'number' ? {
        attempts: preparation.attempts, maxAttempts: preparation.max_attempts, errorCode: asString(preparation.error_code) || null,
      } : null,
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

export const cancelOperation = async (taskId: string, operationId: string): Promise<void> => {
  await api.post(`/hitl/tasks/${taskId}/executions/${operationId}/cancel`)
}

export const rollbackOperation = async (taskId: string, originalId: string, operationId: string, reason: string): Promise<void> => {
  await api.post(`/hitl/tasks/${taskId}/executions/${originalId}/rollback`, { operation_id: operationId, reason })
}

const isHash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)

export const fetchOperation = async (expected: HITLOperationContext, operationId: string, signal?: AbortSignal): Promise<HITLOperation> => {
  const response = await api.get(`/hitl/tasks/${encodeURIComponent(expected.taskId)}/executions/${encodeURIComponent(operationId)}`, { signal })
  const operation = asObject(response.data)
  const record = asObject(operation.record)
  if (!expected.recommendationVersionId || !expected.contentHash || operation.operation_id !== operationId || record.operation_id !== operationId ||
      record.task_id !== expected.taskId || record.project_id !== expected.projectId ||
      record.recommendation_version_id !== expected.recommendationVersionId || record.content_hash !== expected.contentHash ||
      !isHash(operation.record_hash) || typeof operation.state !== 'string' ||
      (record.operation_type !== undefined && record.operation_type !== 'apply' && record.operation_type !== 'rollback')) {
    throw new Error('operation_binding_invalid')
  }
  let receipt: HITLCMSReceipt | null = null
  const rollback = record.operation_type === 'rollback'
  if (operation.receipt !== null && operation.receipt !== undefined) {
    const raw = asObject(operation.receipt)
    const command = asObject(record.command)
    if (raw.protocol !== 'seo-master/v2' || raw.kind !== 'receipt' || raw.operation_id !== operationId ||
        raw.project_id !== expected.projectId || raw.task_id !== expected.taskId || raw.proposal_version !== expected.recommendationVersionId ||
        !isHash(record.request_hash) || raw.request_hash !== record.request_hash || raw.proposal_hash !== command.proposal_hash ||
        typeof command.site_id !== 'string' || raw.site_id !== command.site_id ||
        typeof command.entity_id !== 'number' || raw.entity_id !== command.entity_id ||
        typeof command.approval_ref !== 'string' || command.approval_ref !== record.approval_id || raw.approval_ref !== command.approval_ref ||
        !isHash(raw.signature) ||
        (rollback ? (raw.operation_type !== 'rollback' || raw.outcome === 'applied_unverified' ||
          ['original_operation_id', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash', 'reason']
            .some(key => typeof record[key] !== 'string' || command[key] !== record[key] || raw[key] !== record[key]))
          : (raw.operation_type !== undefined && raw.operation_type !== 'apply') || raw.outcome === 'rollback_applied_unverified') ||
        !isHash(raw.receipt_id) || !isHash(raw.before_digest) || !isHash(raw.after_digest) ||
        raw.before_digest !== command.before_digest || raw.after_digest !== command.expected_after_digest ||
        (raw.outcome !== 'applied_unverified' && raw.outcome !== 'rollback_applied_unverified' && raw.outcome !== 'no_op') ||
        typeof raw.applied_at !== 'string' || !Number.isFinite(Date.parse(raw.applied_at)) ||
        !Array.isArray(raw.applied_fields) || !raw.applied_fields.every((field: unknown) => typeof field === 'string' && ['title', 'description', 'jsonld'].includes(field))) {
      throw new Error('operation_receipt_invalid')
    }
    receipt = { receiptId: raw.receipt_id, operationId, outcome: raw.outcome, appliedAt: raw.applied_at,
      requestHash: record.request_hash, beforeDigest: raw.before_digest, afterDigest: raw.after_digest, appliedFields: raw.applied_fields }
  }
  const confirmed = ['applied_unverified', 'no_op', 'verifying', 'verified', 'verification_failed', 'rollback_in_progress', 'rolled_back',
    'rollback_applied_unverified', 'rollback_verifying', 'rollback_verified', 'rollback_verification_failed']
  if ((confirmed.includes(operation.state) && !receipt) ||
      (rollback && ['applied_unverified', 'verifying', 'verified', 'verification_failed', 'rollback_in_progress', 'rolled_back'].includes(operation.state)) ||
      (!rollback && ['rollback_applied_unverified', 'rollback_verifying', 'rollback_verified', 'rollback_verification_failed'].includes(operation.state))) {
    throw new Error('operation_evidence_invalid')
  }
  if (operation.state === 'verified' || operation.state === 'rollback_verified') {
    const verification = asObject(operation.verification)
    const result = asObject(verification.last_result)
    const observation = asObject(result.observation)
    const target = asObject(asObject(record.version).target).url
    const checks = Array.isArray(result.checks) ? result.checks.map(asObject) : []
    const version = asObject(record.version)
    const changes = Array.isArray(version.changes) ? version.changes.map(asObject) : []
    const fields = new Set(['http', ...changes.filter(change => change.mode !== 'unchanged').map(change => change.cms_field)])
    if (!normalizeVerification(operation.verification) || verification.status !== 'matched' || result.error_code !== null || typeof target !== 'string' ||
        observation.status !== 200 || observation.url !== target || observation.final_url !== target ||
        typeof observation.observed_at !== 'string' || !Number.isFinite(Date.parse(observation.observed_at)) ||
        !isHash(observation.body_sha256) || observation.facts === null || typeof observation.facts !== 'object' ||
        asObject(observation.headers)['content-type']?.toString().split(';')[0].trim().toLowerCase() !== 'text/html' ||
        checks.length < 2 || checks.length > 4 || checks.filter(check => check.field === 'http').length !== 1 ||
        fields.size !== checks.length || checks.some(check => !fields.has(check.field)) ||
        new Set(checks.map(check => check.field)).size !== checks.length ||
        checks.some(check => check.matches !== true || !['http', 'title', 'description', 'jsonld'].includes(asString(check.field)))) {
      throw new Error('operation_evidence_invalid')
    }
  }
  return { operationId, state: operation.state, operationType: record.operation_type === 'rollback' ? 'rollback' : 'apply',
    recordHash: operation.record_hash, requestHash: isHash(record.request_hash) ? record.request_hash : null,
    before: record.before, after: record.after, receipt, verification: normalizeVerification(operation.verification) }
}
