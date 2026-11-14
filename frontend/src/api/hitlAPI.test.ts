import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = {
  get: vi.fn(),
  post: vi.fn(),
}

vi.mock('./axiosConfig', () => ({
  default: apiMock,
}))

const operationWire = (rollback = false) => {
  const refs = rollback ? { original_operation_id: 'original', original_receipt_id: '2'.repeat(64),
    original_request_hash: '3'.repeat(64), original_proposal_hash: '4'.repeat(64), reason: 'Restore' } : {}
  return { operation_id: 'operation-1', state: rollback ? 'rollback_applied_unverified' : 'applied_unverified', record_hash: 'b'.repeat(64),
    verification: null as unknown,
    record: { operation_id: 'operation-1', task_id: 'task-1', project_id: 'project-1', recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64),
      operation_type: rollback ? 'rollback' : 'apply', approval_id: 'approval-1', request_hash: 'c'.repeat(64), ...refs,
      version: { target: { url: 'https://example.com/page' }, changes: [{ mode: 'set', cms_field: 'title' }] },
      command: { site_id: 'site-1', entity_id: 42, approval_ref: 'approval-1', proposal_hash: 'd'.repeat(64), before_digest: 'e'.repeat(64), expected_after_digest: 'f'.repeat(64), ...refs } },
    receipt: { protocol: 'seo-master/v2', kind: 'receipt', operation_id: 'operation-1', project_id: 'project-1', task_id: 'task-1', proposal_version: 'version-1',
      site_id: 'site-1', entity_id: 42, approval_ref: 'approval-1', operation_type: rollback ? 'rollback' : 'apply',
      receipt_id: '1'.repeat(64), request_hash: 'c'.repeat(64), proposal_hash: 'd'.repeat(64), before_digest: 'e'.repeat(64), after_digest: 'f'.repeat(64),
      signature: '5'.repeat(64), outcome: rollback ? 'rollback_applied_unverified' : 'applied_unverified', applied_at: '2026-11-14T10:00:00Z', applied_fields: ['title'], ...refs } }
}

const verifiedEvidence = () => ({ status: 'matched', attempts: 1, max_attempts: 8, deadline_at: '2026-11-14T10:05:00Z',
  last_result: { error_code: null, checks: [{ field: 'http', matches: true }, { field: 'title', matches: true }],
    observation: { url: 'https://example.com/page', final_url: 'https://example.com/page', status: 200, observed_at: '2026-11-14T10:00:01Z',
      body_sha256: '6'.repeat(64), facts: { titles: ['New'] }, headers: { 'content-type': 'text/html' } } } })

const operationContext = { taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }

describe('hitlAPI', () => {
  beforeEach(() => {
    apiMock.get.mockReset()
    apiMock.post.mockReset()
  })

  it('normalizes HITL task payloads from the gateway', async () => {
    const { fetchHITLTasks } = await import('./hitlAPI')
    apiMock.get.mockResolvedValueOnce({
      data: {
        approvals: [
          {
            id: 'approval-1',
            task_id: 'task-1',
            project_id: 'project-1',
            status: 'PENDING',
            execution: { operation_id: 'stable-operation', state: 'needs_reconciliation', recommendation_version_id: 'visible-version', content_hash: 'a'.repeat(64),
              prepared: true, preparation: { attempts: 1, max_attempts: 4, error_code: null },
              recovery: { attempts: 4, max_attempts: 4, last_error: 'receipt_invalid', stop_reason: 'attempts_exhausted' } },
            authorization_status: 'pending',
            diff_data: {
              before: { title: 'Old title' },
              after: { title: 'New title' },
              recommendation_version: { version_id: 'visible-version', content_hash: 'a'.repeat(64) },
            },
            impact_score: '72.5',
            recommendation: 'Review title update',
            metadata: { correlation_id: 'corr-1', recommendation_version_id: 'unrelated-metadata-version' },
            task: {
              id: 'task-1',
              title: 'Meta title update',
              url: 'https://example.com/page',
              task_type: 'UPDATE_META',
            },
          },
        ],
      },
    })

    const tasks = await fetchHITLTasks()

    expect(apiMock.get).toHaveBeenCalledWith('/hitl/tasks', {
      params: { status_filter: 'all', limit: 50 },
    })
    expect(tasks).toHaveLength(1)
    expect(tasks[0]).toMatchObject({
      id: 'approval-1',
      taskId: 'task-1',
      projectId: 'project-1',
      status: 'pending',
      authorizationStatus: 'pending',
      execution: { operationId: 'stable-operation', state: 'needs_reconciliation', prepared: true,
        preparation: { attempts: 1, maxAttempts: 4, errorCode: null }, recovery: {
        attempts: 4, maxAttempts: 4, lastError: 'receipt_invalid', stopReason: 'attempts_exhausted',
      } },
      recommendationVersionId: 'visible-version',
      contentHash: 'a'.repeat(64),
      impactScore: 72.5,
      recommendation: 'Review title update',
      diffData: {
        before: { title: 'Old title' },
        after: { title: 'New title' },
      },
      task: {
        id: 'task-1',
        title: 'Meta title update',
        url: 'https://example.com/page',
        taskType: 'UPDATE_META',
      },
    })
  })

  it('sends approve and reject decisions to the expected endpoints', async () => {
    const { approveTask, rejectTask } = await import('./hitlAPI')
    apiMock.post.mockResolvedValue({})

    await approveTask({ taskId: 'task-1', approvalId: 'approval-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64), approved: true, comment: 'Looks good' })
    await rejectTask({ taskId: 'task-2', approvalId: 'approval-2', recommendationVersionId: 'version-2', contentHash: 'b'.repeat(64), approved: false, comment: 'Needs rewrite' })

    expect(apiMock.post).toHaveBeenNthCalledWith(1, '/hitl/tasks/task-1/approve', {
      approval_id: 'approval-1', recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64),
      comment: 'Looks good',
    })
    expect(apiMock.post).toHaveBeenNthCalledWith(2, '/hitl/tasks/task-2/reject', {
      approval_id: 'approval-2', recommendation_version_id: 'version-2', content_hash: 'b'.repeat(64),
      comment: 'Needs rewrite',
    })
  })

  it('applies references only with a stable operation ID and no mutable payload', async () => {
    const { applyTask } = await import('./hitlAPI')
    apiMock.post.mockResolvedValueOnce({})
    await applyTask({ taskId: 'task-1', approvalId: 'approval-1', recommendationVersionId: 'version-1',
      contentHash: 'a'.repeat(64), approved: true, comment: 'not a CMS payload' }, 'operation-1')
    expect(apiMock.post).toHaveBeenCalledWith('/hitl/tasks/task-1/apply', {
      operation_id: 'operation-1', task_id: 'task-1', approval_id: 'approval-1',
      recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64),
    })
  })

  it('reads the scoped operation and preserves missing, empty and null snapshots without a write', async () => {
    const { fetchOperation } = await import('./hitlAPI')
    const expected = { taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }
    const before = { fields: { title: { state: 'missing' }, description: { state: 'value', value: '' }, jsonld: null } }
    apiMock.get.mockResolvedValueOnce({ data: { operation_id: 'operation-1', state: 'ready', record_hash: 'b'.repeat(64),
      record: { operation_id: 'operation-1', task_id: 'task-1', project_id: 'project-1', recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64),
        before, after: { fields: { title: { state: 'value', value: '<script>unsafe()</script>' } } }, request_hash: 'c'.repeat(64) }, receipt: null } })
    const signal = new AbortController().signal
    const result = await fetchOperation(expected, 'operation-1', signal)
    expect(apiMock.get).toHaveBeenCalledWith('/hitl/tasks/task-1/executions/operation-1', { signal })
    expect(result.before).toEqual(before)
    expect(result.state).toBe('ready')
    expect(apiMock.post).not.toHaveBeenCalled()
  })

  it.each(['task_id', 'project_id', 'recommendation_version_id', 'content_hash', 'operation_id'])('rejects an operation bound to another %s before display', async (key) => {
    const { fetchOperation } = await import('./hitlAPI')
    const record = { task_id: 'task-1', project_id: 'project-1', recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64), operation_id: 'operation-1' }
    apiMock.get.mockResolvedValueOnce({ data: { operation_id: 'operation-1', state: 'verified', record_hash: 'b'.repeat(64), record: { ...record, [key]: 'foreign' } } })
    await expect(fetchOperation({ taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }, 'operation-1')).rejects.toThrow('operation_binding_invalid')
  })

  it('does not display a receipt whose request hash differs from the saved operation', async () => {
    const { fetchOperation } = await import('./hitlAPI')
    const record = { task_id: 'task-1', project_id: 'project-1', recommendation_version_id: 'version-1', content_hash: 'a'.repeat(64), operation_id: 'operation-1', request_hash: 'c'.repeat(64) }
    apiMock.get.mockResolvedValueOnce({ data: { operation_id: 'operation-1', state: 'verified', record_hash: 'b'.repeat(64), record,
      receipt: { protocol: 'seo-master/v2', kind: 'receipt', operation_id: 'operation-1', request_hash: 'd'.repeat(64) } } })
    await expect(fetchOperation({ taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }, 'operation-1')).rejects.toThrow('operation_receipt_invalid')
  })

  it.each(['apply', 'rollback'])('reads a linked %s receipt without deriving verified from its existence', async (kind) => {
    const { fetchOperation } = await import('./hitlAPI')
    const expected = { taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }
    const rollbackRefs = kind === 'rollback' ? { original_operation_id: 'original', original_receipt_id: '2'.repeat(64),
      original_request_hash: '3'.repeat(64), original_proposal_hash: '4'.repeat(64), reason: 'Restore' } : {}
    const record = { task_id: 'task-1', project_id: 'project-1', recommendation_version_id: 'version-1', content_hash: expected.contentHash, approval_id: 'approval-1', ...rollbackRefs,
      operation_id: 'operation-1', operation_type: kind, request_hash: 'c'.repeat(64),
      command: { proposal_hash: 'd'.repeat(64), before_digest: 'e'.repeat(64), expected_after_digest: 'f'.repeat(64),
        site_id: 'site-1', entity_id: 42, approval_ref: 'approval-1', ...rollbackRefs } }
    const outcome = kind === 'rollback' ? 'rollback_applied_unverified' : 'applied_unverified'
    apiMock.get.mockResolvedValueOnce({ data: { operation_id: 'operation-1', state: outcome, record_hash: 'b'.repeat(64), record,
      receipt: { protocol: 'seo-master/v2', kind: 'receipt', operation_id: 'operation-1', project_id: 'project-1', task_id: 'task-1',
        site_id: 'site-1', entity_id: 42, approval_ref: 'approval-1', ...(kind === 'rollback' ? { operation_type: 'rollback' } : {}),
        ...rollbackRefs, signature: '5'.repeat(64),
        proposal_version: 'version-1', request_hash: record.request_hash, proposal_hash: record.command.proposal_hash,
        receipt_id: '1'.repeat(64), before_digest: record.command.before_digest, after_digest: record.command.expected_after_digest,
        outcome, applied_at: '2026-11-14T10:00:00Z', applied_fields: ['title'] } } })
    const result = await fetchOperation(expected, 'operation-1')
    expect(result.receipt?.outcome).toBe(outcome)
    expect(result.state).toBe(outcome)
    expect(result.verification).toBeNull()
  })

  it('rejects verified wire without its own receipt and public evidence', async () => {
    const { fetchOperation } = await import('./hitlAPI')
    const context = { taskId: 'task-1', projectId: 'project-1', recommendationVersionId: 'version-1', contentHash: 'a'.repeat(64) }
    apiMock.get.mockResolvedValueOnce({ data: { operation_id: 'operation-1', state: 'verified', record_hash: 'b'.repeat(64), receipt: null,
      verification: null, record: { task_id: context.taskId, project_id: context.projectId, recommendation_version_id: context.recommendationVersionId,
        content_hash: context.contentHash, operation_id: 'operation-1' } } })
    await expect(fetchOperation(context, 'operation-1')).rejects.toThrow('operation_evidence_invalid')
  })

  it.each(['site_id', 'entity_id', 'approval_ref', 'operation_type', 'outcome'])('rejects a receipt with a substituted %s', async (field) => {
    const { fetchOperation } = await import('./hitlAPI')
    const wire = operationWire()
    Object.assign(wire.receipt, { [field]: field === 'entity_id' ? 999 : 'foreign' })
    apiMock.get.mockResolvedValueOnce({ data: wire })
    await expect(fetchOperation(operationContext, 'operation-1')).rejects.toThrow('operation_receipt_invalid')
  })

  it.each(['original_operation_id', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash', 'reason', 'outcome'])('rejects rollback receipt substitution of %s', async (field) => {
    const { fetchOperation } = await import('./hitlAPI')
    const wire = operationWire(true)
    Object.assign(wire.receipt, { [field]: field === 'outcome' ? 'applied_unverified' : 'foreign' })
    apiMock.get.mockResolvedValueOnce({ data: wire })
    await expect(fetchOperation(operationContext, 'operation-1')).rejects.toThrow('operation_receipt_invalid')
  })

  it.each(['pending', 'mismatch', 'wrong_url', 'http_error', 'empty_checks', 'partial_checks'])('rejects verified with %s evidence', async (fault) => {
    const { fetchOperation } = await import('./hitlAPI')
    const wire = operationWire()
    wire.state = 'verified'
    const evidence = verifiedEvidence()
    if (fault === 'pending') evidence.status = 'pending'
    if (fault === 'mismatch') evidence.last_result.checks[1].matches = false
    if (fault === 'wrong_url') evidence.last_result.observation.url = 'https://foreign.example/'
    if (fault === 'http_error') evidence.last_result.observation.status = 503
    if (fault === 'empty_checks') evidence.last_result.checks = []
    if (fault === 'partial_checks') wire.record.version.changes.push({ mode: 'set', cms_field: 'description' })
    wire.verification = evidence
    apiMock.get.mockResolvedValueOnce({ data: wire })
    await expect(fetchOperation(operationContext, 'operation-1')).rejects.toThrow('operation_evidence_invalid')
  })

  it.each([false, true])('shows the authoritative verified state only with consistent receipt and public evidence, rollback=%s', async rollback => {
    const { fetchOperation } = await import('./hitlAPI')
    const wire = operationWire(rollback)
    wire.state = rollback ? 'rollback_verified' : 'verified'
    wire.verification = verifiedEvidence()
    apiMock.get.mockResolvedValueOnce({ data: wire })
    const result = await fetchOperation(operationContext, 'operation-1')
    expect(result.state).toBe(wire.state)
    expect(result.verification?.observation?.status).toBe(200)
  })
})

