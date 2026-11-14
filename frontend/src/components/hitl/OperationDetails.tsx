import { useEffect, useState } from 'react'
import { fetchOperation } from '../../api/hitlAPI'
import type { HITLOperation, HITLOperationContext } from '../../types/hitl'
import Button from '../ui/Button'

interface Props {
  context: HITLOperationContext
  operationId: string
  label: string
}

const snapshotText = (value: unknown) => value === undefined ? 'Снимок ещё не сохранён.' : JSON.stringify(value, null, 2)

export default function OperationDetails({ context, operationId, label }: Props) {
  const [open, setOpen] = useState(false)
  const [revision, setRevision] = useState(0)
  const [loaded, setLoaded] = useState<{ identity: string; operation: HITLOperation } | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(false)
  const { taskId, projectId, recommendationVersionId, contentHash } = context
  const identity = JSON.stringify([taskId, projectId, recommendationVersionId, contentHash, operationId])
  const result = loaded?.identity === identity ? loaded.operation : null

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setLoaded(null)
    setError(false)
    setLoading(true)
    void fetchOperation({ taskId, projectId, recommendationVersionId, contentHash }, operationId, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) setLoaded({ identity, operation: value })
      }).catch(() => {
        if (!controller.signal.aborted) setError(true)
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [open, revision, taskId, projectId, recommendationVersionId, contentHash, operationId, identity])

  return <section className="mt-4 rounded-lg border border-gray-200 p-4" aria-label={label}>
    <Button type="button" variant="outline" aria-expanded={open} onClick={() => setOpen(value => !value)}>{label}</Button>
    {open && <div className="mt-3 space-y-3 text-sm text-gray-700">
      <p className="break-all">Операция: {operationId}</p>
      <Button type="button" variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}>Обновить данные операции</Button>
      {loading && <p>Загружаем сохранённые данные операции…</p>}
      {error && <p role="alert">Данные операции недоступны или не соответствуют этой версии. Обновите карточку; при отказе доступа проверьте сессию и права.</p>}
      {result && <>
        <p>Состояние на момент чтения: <code>{result.state}</code>.</p>
        <p className="break-all">Hash сохранённой записи: <code>{result.recordHash}</code></p>
        {result.requestHash && <p className="break-all">Hash CMS-команды: <code>{result.requestHash}</code></p>}
        {result.receipt ? <div className="space-y-1 rounded border border-gray-200 p-3" aria-label="Квитанция CMS">
          <p className="break-all">Receipt ID: <code>{result.receipt.receiptId}</code></p>
          <p>Результат CMS: <code>{result.receipt.outcome}</code>; время: {result.receipt.appliedAt}.</p>
          <p>Изменённые поля: {result.receipt.appliedFields.length ? result.receipt.appliedFields.join(', ') : 'нет (no-op)'}.</p>
          <p className="break-all">Digest до: <code>{result.receipt.beforeDigest}</code></p>
          <p className="break-all">Digest после: <code>{result.receipt.afterDigest}</code></p>
          <p>Квитанция подтверждает результат CMS. Публичное состояние подтверждается отдельной проверкой.</p>
        </div> : <p>Подтверждённой квитанции CMS пока нет. Успешный HTTP-ответ сам по себе не подтверждает публикацию.</p>}
        {result.verification?.observation && <p>Публичное чтение: {result.verification.observation.url}, HTTP {result.verification.observation.status}, {result.verification.observation.observedAt}.</p>}
        {result.verification?.checks.map(check => <p key={check.field}>{check.field}: {check.matches ? 'совпадает' : 'расхождение'}</p>)}
        <details><summary>Снимок до записи</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all">{snapshotText(result.before)}</pre></details>
        <details><summary>Снимок после записи</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all">{snapshotText(result.after)}</pre></details>
      </>}
    </div>}
  </section>
}
