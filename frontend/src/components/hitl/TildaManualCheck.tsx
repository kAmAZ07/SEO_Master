import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import api from '../../api/axiosConfig'
import type { HITLTask } from '../../types/hitl'

interface Check {
  check_id: string; task_id: string; project_id: string; recommendation_version_id: string; content_hash: string
  status: string; receipt: null
  result: { error_code?: string | null; observation?: { url: string; final_url: string; observed_at: string; status: number }; checks?: { field: string; matches: boolean }[] }
}

export default function TildaManualCheck({ task }: { task: HITLTask }) {
  const [id, setId] = useState<string | null>(null)
  const [check, setCheck] = useState<Check | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [retryAllowed, setRetryAllowed] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const request = async (readOnly: boolean) => {
    const checkId = id || crypto.randomUUID()
    setId(checkId); setBusy(true); setError(null); setRetryAllowed(false)
    try {
      const response = readOnly
        ? await api.get<Check>(`/hitl/tasks/${encodeURIComponent(task.taskId)}/manual-checks/${checkId}`)
        : await api.post<Check>(`/hitl/tasks/${encodeURIComponent(task.taskId)}/manual-verify`, {
          check_id: checkId, approval_id: task.id, recommendation_version_id: task.recommendationVersionId, content_hash: task.contentHash,
        })
      const value = response.data
      const expectedURL = task.task?.url || task.metadata.url
      const fields = new Set(['http', ...(task.manualCheckFields ?? [])])
      if (value.check_id !== checkId || value.task_id !== task.taskId || value.project_id !== task.projectId ||
          value.recommendation_version_id !== task.recommendationVersionId || value.content_hash !== task.contentHash ||
          value.receipt !== null || !['manual_verified', 'manual_mismatch', 'manual_check_failed', 'manual_checking', 'manual_check_unknown'].includes(value.status) ||
          (value.status === 'manual_verified' && (value.result?.error_code !== null || value.result.observation?.status !== 200 ||
            !expectedURL || value.result.observation.url !== expectedURL || value.result.observation.final_url !== expectedURL ||
            fields.size < 2 || value.result.checks?.length !== fields.size ||
            new Set(value.result.checks.map(item => item.field)).size !== fields.size ||
            !value.result.checks.every(item => fields.has(item.field)) ||
            !value.result.checks.every(item => item.matches === true && ['http', 'title', 'description', 'jsonld'].includes(item.field)))))
        throw new Error('invalid_manual_check')
      if (alive.current) setCheck(value)
    } catch (failure) {
      if (alive.current) {
        const missing = isAxiosError(failure) && failure.response?.status === 404 &&
          failure.response.data?.error?.message === 'manual_check_not_found'
        setRetryAllowed(readOnly && missing)
        setError('Результат проверки не подтверждён. Прочитайте статус той же проверки; публикация не выполнялась.')
      }
    } finally { if (alive.current) setBusy(false) }
  }
  return <section aria-label="Проверка ручного внедрения Tilda" className="mt-3 rounded-lg border border-amber-300 p-3">
    <p>write_unsupported / manual_required. Внесите одобренный diff в редакторе Tilda и опубликуйте вручную.</p>
    {!id && <button type="button" disabled={busy || task.authorizationStatus !== 'approved'} onClick={() => void request(false)}>Проверить ручное внедрение независимо</button>}
    {id && <button type="button" disabled={busy} onClick={() => void request(true)}>Прочитать статус ручной проверки</button>}
    {retryAllowed && <button type="button" disabled={busy || task.authorizationStatus !== 'approved'} onClick={() => void request(false)}>Повторить запрос той же проверки</button>}
    {check && <>
      <p role="status">{check.status === 'manual_verified' ? 'Ручное изменение совпало с одобренной версией. CMS-квитанция отсутствует.' : `Ручная проверка: ${check.status}`}</p>
      {check.result.observation && <p>Публичное чтение: {check.result.observation.url}; {check.result.observation.observed_at}</p>}
      {check.status !== 'manual_checking' && <button type="button" disabled={busy} onClick={() => { setId(null); setCheck(null); setError(null) }}>Новая независимая проверка</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>
}
