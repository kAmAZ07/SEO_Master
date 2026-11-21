import { useCallback, useEffect, useRef, useState } from 'react'
import api from '../../api/axiosConfig'

interface Job {
  job_id: string; status: string; created_at: string; error_code: string | null
  result: { observed_at?: string; page_count?: number; pages?: { page_id: string; url: string; fields: Record<string, unknown> }[] }
}
interface Source {
  project_id: string; callback_url: string | null; can_sync: boolean; jobs: Job[]; reader_state: 'active' | 'disabled' | 'unavailable'
  capabilities: { mode: string; write_status: string; requests_per_hour: number; required_plan: string; configured_requests_per_hour: number }
}

export default function TildaReadPanel({ projectId }: { projectId: string }) {
  const [source, setSource] = useState<Source | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCallback, setShowCallback] = useState(false)
  const [directory, setDirectory] = useState<{ project_id: string; items: { page_id: string; url: string; fields: Record<string, unknown> }[]; total: number | null; observed_at: string | null } | null>(null)
  const [offset, setOffset] = useState(0)
  const activeProject = useRef(projectId)
  const generation = useRef(0)
  activeProject.current = projectId
  const current = source?.project_id === projectId ? source : null
  const currentDirectory = directory?.project_id === projectId ? directory : null

  const read = useCallback(async (signal?: AbortSignal) => {
    const requestGeneration = ++generation.current
    const response = await api.get<Source>(`/projects/${encodeURIComponent(projectId)}/tilda`, { signal })
    if (signal?.aborted || activeProject.current !== projectId || generation.current !== requestGeneration) return
    const data = response.data
    if (data.project_id !== projectId || data.capabilities?.mode !== 'read_only_manual' ||
        data.capabilities.write_status !== 'write_unsupported' || !Array.isArray(data.jobs) ||
        !['active', 'disabled', 'unavailable'].includes(data.reader_state) ||
        typeof data.can_sync !== 'boolean' || data.jobs.some(job => typeof job.job_id !== 'string' ||
          !['queued', 'reading', 'read_retry', 'manual_required', 'read_failed'].includes(job.status)))
      throw new Error('invalid_tilda_source')
    setSource(data)
  }, [projectId])
  useEffect(() => {
    const controller = new AbortController()
    setSource(null); setError(null); setShowCallback(false); setDirectory(null); setOffset(0)
    void read(controller.signal).catch(() => {
      if (!controller.signal.aborted) setError('Статус чтения недоступен. Проверьте сохранённые ключи и права доступа.')
    })
    return () => controller.abort()
  }, [projectId, read])

  const refresh = async (synchronize: boolean) => {
    setBusy(true); setError(null)
    try {
      if (synchronize) await api.post(`/projects/${encodeURIComponent(projectId)}/tilda/sync`)
      await read()
    } catch { setError('Не удалось подтвердить чтение. Обновите статус; публикация не запускалась.') }
    finally { setBusy(false) }
  }
  const readPages = async (nextOffset: number) => {
    setBusy(true); setError(null)
    try {
      const response = await api.get(`/projects/${encodeURIComponent(projectId)}/tilda/pages`, { params: { limit: 50, offset: nextOffset } })
      if (activeProject.current !== projectId) return
      if (response.data.project_id !== projectId || !Array.isArray(response.data.items) ||
          !(response.data.total === null || (Number.isInteger(response.data.total) && response.data.total >= 0))) throw new Error('invalid_directory')
      setDirectory(response.data); setOffset(nextOffset)
    } catch { if (activeProject.current === projectId) setError('Сохранённый список страниц недоступен.') }
    finally { if (activeProject.current === projectId) setBusy(false) }
  }
  return <section aria-label="Tilda read-only Beta" className="mt-5 space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
    <h4 className="font-semibold">Tilda Beta — ручное внедрение</h4>
    <p>write_unsupported: автоматическая публикация не поддерживается. Внесите согласованные изменения в редакторе Tilda и опубликуйте страницу вручную.</p>
    <p>API требует Business-план: до 150 запросов в час. Просмотр этого блока читает сохранённые снимки; синхронизация запускается отдельной кнопкой.</p>
    {current && <p>Обработчик чтения: {current.reader_state === 'active' ? 'работает' : current.reader_state === 'disabled' ? 'отключён' : 'недоступен'}. Настроенный лимит: {current.capabilities.configured_requests_per_hour} запросов в час.</p>}
    <button type="button" disabled={busy} onClick={() => void refresh(false)}>Обновить сохранённый статус Tilda</button>
    {current?.can_sync && <button type="button" disabled={busy} onClick={() => void refresh(true)}>Прочитать страницы Tilda</button>}
    {current && <button type="button" disabled={busy} onClick={() => void readPages(0)}>Список сохранённых страниц Tilda</button>}
    {currentDirectory && <div>
      <p>Каталог на {currentDirectory.observed_at ?? 'неизвестную дату'}: {currentDirectory.total === null ? 'unavailable' : `${offset + currentDirectory.items.length} из ${currentDirectory.total}`}.</p>
      {currentDirectory.items.map(page => <p key={page.page_id}>{page.page_id}: {page.url}</p>)}
      {offset > 0 && <button type="button" disabled={busy} onClick={() => void readPages(Math.max(0, offset - 50))}>Предыдущие страницы Tilda</button>}
      {currentDirectory.total !== null && offset + currentDirectory.items.length < currentDirectory.total && <button type="button" disabled={busy} onClick={() => void readPages(offset + 50)}>Следующие страницы Tilda</button>}
    </div>}
    {current?.callback_url && <>
      <button type="button" onClick={() => setShowCallback(value => !value)}>Показать адрес callback</button>
      {showCallback && <label>Секретный адрес для поля Webhook URL в Tilda
        <input aria-label="Секретный адрес callback Tilda" readOnly value={current.callback_url} className="w-full" />
      </label>}
      <p>Callback сохраняет уведомление для повторного чтения. Он не подтверждает публикацию SEO Master или права на домен.</p>
    </>}
    {current?.jobs.map(job => <article key={job.job_id} className="rounded border p-3">
      <p>Состояние чтения: {job.status}; {job.created_at}</p>
      {job.error_code && <p role="status">Источник недоступен: {job.error_code}</p>}
      {job.result?.observed_at && <p>Снимок источника: {job.result.observed_at}</p>}
      {job.result?.pages?.map(page => <div key={page.page_id}><p>{page.url}</p><pre className="overflow-auto">{JSON.stringify(page.fields, null, 2)}</pre></div>)}
      {job.result.page_count !== undefined && job.result.page_count > (job.result.pages?.length ?? 0) && <p>Показаны первые 50 страниц снимка. Полный сохранённый каталог доступен через список страниц.</p>}
    </article>)}
    <p>После ручного внедрения проверьте публичную страницу независимо. Уведомление Tilda и снимок API сами по себе не означают verified.</p>
    {error && <p role="alert">{error}</p>}
  </section>
}
