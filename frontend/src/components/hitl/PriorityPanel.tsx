import { useState, type FormEvent } from 'react'
import { changePriority, fetchPriorityHistory } from '../../api/hitlAPI'
import type { PriorityChange, PriorityHistory, PrioritySnapshot, PriorityUrgency } from '../../types/hitl'

const urgencyNames: Record<PriorityUrgency, string> = {
  critical: 'Критическая', high: 'Высокая', medium: 'Средняя', low: 'Низкая', unknown: 'Не оценена',
}
const shown = (value: number | null) => value === null ? 'unknown' : String(value)

export default function PriorityPanel({ taskId, priority, onSaved }: {
  taskId: string; priority: PrioritySnapshot | null | undefined; onSaved: () => Promise<unknown>
}) {
  const [action, setAction] = useState<PriorityChange['action']>('estimate')
  const [impact, setImpact] = useState(priority?.inputs.impact?.toString() ?? '')
  const [confidence, setConfidence] = useState(priority?.inputs.confidence?.toString() ?? '')
  const [effort, setEffort] = useState(priority?.inputs.effort?.toString() ?? '')
  const [urgency, setUrgency] = useState<PriorityUrgency>(priority?.inputs.urgency ?? 'unknown')
  const [urgencyReason, setUrgencyReason] = useState(priority?.inputs.urgency_reason ?? '')
  const [score, setScore] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<PriorityHistory[] | null>(null)
  const id = `priority-${taskId}`

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!priority || busy) return
    const base = { expected_revision: priority.revision, reason: reason.trim() }
    const number = (value: string) => value.trim() === '' ? null : Number(value)
    const command: PriorityChange = action === 'estimate' ? { ...base, action, inputs: {
      impact: number(impact), confidence: number(confidence), effort: number(effort), urgency,
      urgency_reason: urgencyReason.trim() || null,
    } } : action === 'override' ? { ...base, action, score: Number(score) } : { ...base, action }
    setBusy(true); setError(null)
    try {
      await changePriority(taskId, command)
      setReason(''); setHistory(null)
      await onSaved()
    } catch {
      setError('Изменение не подтверждено. Обновите очередь и проверьте права, ревизию и введённые значения.')
    } finally { setBusy(false) }
  }

  const readHistory = async () => {
    setBusy(true); setError(null)
    try { setHistory(await fetchPriorityHistory(taskId)) }
    catch { setError('Не удалось получить журнал приоритетов.') }
    finally { setBusy(false) }
  }

  return <details className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
    <summary className="cursor-pointer font-medium">Приоритет: {priority ? shown(priority.effective_score) : 'недоступен'}</summary>
    {!priority ? <p>Данные приоритета недоступны. Обновите очередь.</p> : <>
      <p className="mt-3">priority_v1 = impact × confidence / effort. Эвристическая оценка, не прогноз роста FF-Score.</p>
      <p>Impact: {shown(priority.inputs.impact)}; confidence: {shown(priority.inputs.confidence)}; effort: {shown(priority.inputs.effort)} (1…5).</p>
      <p>Расчёт: {shown(priority.computed_score)}. Срочность: {urgencyNames[priority.inputs.urgency]}; причина: {priority.inputs.urgency_reason ?? 'не задана'}.</p>
      {priority.override && <p>Ручной приоритет {priority.override.score}: {priority.override.actor_id}; {priority.override.reason}; {priority.override.at}</p>}
      <form onSubmit={event => void save(event)} className="mt-3 grid gap-2">
        <label htmlFor={`${id}-action`}>Изменение приоритета</label>
        <select id={`${id}-action`} value={action} onChange={event => setAction(event.target.value as PriorityChange['action'])}>
          <option value="estimate">Оценить входы</option><option value="override">Задать вручную</option><option value="clear_override">Снять ручной приоритет</option>
        </select>
        {action === 'estimate' && <>
          <label htmlFor={`${id}-impact`}>Impact (0…1)</label>
          <input id={`${id}-impact`} type="number" min="0" max="1" step="any" value={impact} onChange={e => setImpact(e.target.value)} />
          <label htmlFor={`${id}-confidence`}>Confidence (0…1)</label>
          <input id={`${id}-confidence`} type="number" min="0" max="1" step="any" value={confidence} onChange={e => setConfidence(e.target.value)} />
          <label htmlFor={`${id}-effort`}>Effort (1…5)</label>
          <input id={`${id}-effort`} type="number" min="1" max="5" step="any" value={effort} onChange={e => setEffort(e.target.value)} />
          <label htmlFor={`${id}-urgency`}>Срочность</label>
          <select id={`${id}-urgency`} value={urgency} onChange={e => setUrgency(e.target.value as PriorityUrgency)}>
            {Object.entries(urgencyNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <label htmlFor={`${id}-urgency-reason`}>Причина срочности</label>
          <input id={`${id}-urgency-reason`} required={urgency !== 'unknown'} value={urgencyReason} onChange={e => setUrgencyReason(e.target.value)} />
          <p>Пустое числовое поле означает unknown. Сохранённый ручной приоритет действует до его явного снятия.</p>
        </>}
        {action === 'override' && <><label htmlFor={`${id}-score`}>Ручной балл (0…1)</label>
          <input id={`${id}-score`} type="number" required min="0" max="1" step="any" value={score} onChange={e => setScore(e.target.value)} /></>}
        <label htmlFor={`${id}-reason`}>Причина изменения</label>
        <input id={`${id}-reason`} required value={reason} onChange={e => setReason(e.target.value)} />
        <button type="submit" disabled={busy || !reason.trim()}>Сохранить приоритет</button>
      </form>
      <button type="button" disabled={busy} className="mt-3" onClick={() => void readHistory()}>Журнал приоритетов</button>
      {history && <><p>Последние {history.length} записей; полный журнал доступен через API с пагинацией.</p>
        <ul>{history.map(row => <li key={row.id}>{row.at}: {row.action}; {row.actor_id}; {row.reason}; {shown(row.before.effective_score)} → {shown(row.after.effective_score)}</li>)}</ul></>}
    </>}
    {error && <p role="alert">{error}</p>}
  </details>
}
