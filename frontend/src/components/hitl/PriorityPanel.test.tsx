import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import PriorityPanel from './PriorityPanel'
import type { PrioritySnapshot } from '../../types/hitl'

const mocks = vi.hoisted(() => ({ change: vi.fn(), history: vi.fn() }))
vi.mock('../../api/hitlAPI', () => ({ changePriority: mocks.change, fetchPriorityHistory: mocks.history }))

const priority: PrioritySnapshot = {
  formula_version: 'priority_v1', revision: 3, inputs: { impact: null, confidence: null, effort: null, urgency: 'unknown', urgency_reason: null },
  computed_score: null, effective_score: null, urgency_rank: 4, status: 'unknown', heuristic: true, override: null,
}
const setup = async (value = priority) => {
  const onSaved = vi.fn().mockResolvedValue(undefined)
  render(<PriorityPanel taskId="task-1" priority={value} onSaved={onSaved} />)
  await userEvent.click(screen.getByText(/^Приоритет:/))
  return onSaved
}
beforeEach(() => vi.resetAllMocks())

it('keeps unknown values and sends blank estimates as null with exact revision', async () => {
  mocks.change.mockResolvedValue({ ...priority, revision: 4 })
  const saved = await setup()
  expect(screen.getByText(/Impact: unknown; confidence: unknown; effort: unknown/)).toBeVisible()
  expect(mocks.change).not.toHaveBeenCalled()
  await userEvent.type(screen.getByLabelText('Причина изменения'), 'Need evidence')
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить приоритет' }))
  await waitFor(() => expect(saved).toHaveBeenCalledOnce())
  expect(mocks.change).toHaveBeenCalledWith('task-1', { action: 'estimate', expected_revision: 3, reason: 'Need evidence', inputs: priority.inputs })
})

it('requires a reason and preserves explicit zero override without an actor in the payload', async () => {
  mocks.change.mockResolvedValue({ ...priority, revision: 4 })
  await setup()
  await userEvent.selectOptions(screen.getByLabelText('Изменение приоритета'), 'override')
  await userEvent.type(screen.getByLabelText('Ручной балл (0…1)'), '0')
  expect(screen.getByRole('button', { name: 'Сохранить приоритет' })).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Причина изменения'), 'Defer after review')
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить приоритет' }))
  expect(mocks.change).toHaveBeenCalledWith('task-1', { action: 'override', expected_revision: 3, score: 0, reason: 'Defer after review' })
})

it('reports conflicts without claiming a saved result or changing the revision', async () => {
  mocks.change.mockRejectedValue(new Error('409'))
  const saved = await setup()
  await userEvent.type(screen.getByLabelText('Причина изменения'), 'Triage')
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить приоритет' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Изменение не подтверждено')
  expect(saved).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Причина изменения')).toHaveValue('Triage')
})

it('renders audit actor/reason as inert text and distinguishes zero from unknown', async () => {
  const after = { ...priority, effective_score: 0 }
  mocks.history.mockResolvedValue([{ id: 'entry', action: 'override', actor_id: 'owner', reason: '<img src=x onerror=alert(1)>',
    revision: 4, at: '2026-11-15T12:00:00Z', before: priority, after }])
  await setup()
  await userEvent.click(screen.getByRole('button', { name: 'Журнал приоритетов' }))
  expect(await screen.findByText(/owner; <img src=x onerror=alert\(1\)>; unknown → 0/)).toBeVisible()
  expect(document.querySelector('img')).toBeNull()
})
