import { useEffect, useMemo, useState } from 'react'
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'
import { applyHITLTask, approveHITLTask, cancelHITLOperation, loadHITLTasks, rejectHITLTask } from '../store/slices/hitlSlice'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import type { HITLTask } from '../types/hitl'

const formatJson = (value?: Record<string, unknown>) => {
  if (!value || Object.keys(value).length === 0) {
    return '{}'
  }
  return JSON.stringify(value, null, 2)
}

const formatImpact = (score?: number | null) => {
  if (score == null) {
    return 'n/a'
  }

  const normalized = score <= 1 ? score * 100 : score
  return `${Math.round(normalized)}%`
}

const getTaskTitle = (task: HITLTask) => task.task?.title || task.recommendation || 'SEO change approval'

const getTaskDescription = (task: HITLTask) =>
  task.task?.description || task.recommendation || 'Проверьте предлагаемое изменение перед публикацией.'

const getTaskUrl = (task: HITLTask) => task.task?.url || task.metadata.url || task.metadata.root_url

const getReadableMeta = (value: unknown) => {
  if (value === null || value === undefined || value === '') {
    return 'n/a'
  }
  return String(value)
}

const HITL = () => {
  const dispatch = useAppDispatch()
  const { tasks, loading, error } = useAppSelector((state) => state.hitl)
  const [comments, setComments] = useState<Record<string, string>>({})
  const [processingTaskId, setProcessingTaskId] = useState<string | null>(null)
  const [operationIds, setOperationIds] = useState<Record<string, string>>({})

  useEffect(() => {
    void dispatch(loadHITLTasks())
  }, [dispatch])

  const sortedTasks = useMemo(
    () => [...tasks].sort((left, right) => (right.impactScore ?? 0) - (left.impactScore ?? 0)),
    [tasks],
  )

  const handleDecision = async (task: HITLTask, approved: boolean) => {
    if (!task.recommendationVersionId || !task.contentHash || task.authorizationStatus !== 'pending') return
    const comment = comments[task.taskId]?.trim()
    const decision = { taskId: task.taskId, approvalId: task.id,
      recommendationVersionId: task.recommendationVersionId, contentHash: task.contentHash, approved, comment }
    setProcessingTaskId(task.taskId)

    try {
      if (approved) {
        await dispatch(approveHITLTask(decision)).unwrap()
      } else {
        await dispatch(rejectHITLTask(decision)).unwrap()
      }
    } catch {
      // The thunk stores the server error, displayed by this page.
      return
    } finally {
      setProcessingTaskId(null)
    }
  }

  const handleApply = async (task: HITLTask) => {
    if (!task.recommendationVersionId || !task.contentHash) return
    const key = `${task.id}:${task.recommendationVersionId}`
    const expired = task.execution && ['expired', 'cancelled'].includes(task.execution.state)
    const remembered = operationIds[key]
    const operationId = expired
      ? (remembered && remembered !== task.execution?.operationId ? remembered : crypto.randomUUID())
      : task.execution?.operationId || remembered || crypto.randomUUID()
    setOperationIds(current => ({ ...current, [key]: operationId }))
    setProcessingTaskId(task.taskId)
    try {
      await dispatch(applyHITLTask({ operationId, approval: {
        taskId: task.taskId, approvalId: task.id, recommendationVersionId: task.recommendationVersionId,
        contentHash: task.contentHash, approved: true,
      } })).unwrap()
    } catch {
      // Keep operationId for status reconciliation; the thunk displays the failure.
      return
    } finally {
      setProcessingTaskId(null)
    }
  }

  const handleCancel = async (task: HITLTask) => {
    if (!task.execution) return
    setProcessingTaskId(task.taskId)
    try {
      await dispatch(cancelHITLOperation({ taskId: task.taskId, operationId: task.execution.operationId })).unwrap()
    } catch {
      // The thunk shows an authority conflict; no local state is guessed.
    } finally {
      setProcessingTaskId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">Human in the loop</p>
          <h1 className="mt-1 text-3xl font-bold text-gray-900">HITL-согласования</h1>
          <p className="mt-2 max-w-3xl text-gray-600">
            Приватные карточки согласования показывают предлагаемое изменение, impact и diff до публикации через API Gateway.
          </p>
        </div>
        <Button type="button" variant="outline" onClick={() => void dispatch(loadHITLTasks())} disabled={loading}>
          Обновить
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && sortedTasks.length === 0 ? (
        <Card className="p-10 text-center">
          <Loader />
          <p className="mt-4 text-gray-600">Загружаем задачи на согласование...</p>
        </Card>
      ) : sortedTasks.length === 0 ? (
        <Card className="p-10 text-center">
          <h2 className="text-xl font-semibold text-gray-900">Нет ожидающих HITL-задач</h2>
          <p className="mt-2 text-gray-600">Когда оркестратор создаст задачу на ручное одобрение, она появится здесь.</p>
        </Card>
      ) : (
        <div className="space-y-6">
          {sortedTasks.map((task) => {
            const taskUrl = getTaskUrl(task)
            const isProcessing = processingTaskId === task.taskId
            const canDecide = task.authorizationStatus === 'pending' && !!task.recommendationVersionId && !!task.contentHash
            const reconcile = task.execution && ['applying', 'outcome_unknown'].includes(task.execution.state)
            const preparing = task.execution && ['queued', 'preflight', 'dry_run'].includes(task.execution.state)
            const canCancel = task.execution && ['queued', 'preflight', 'dry_run', 'ready', 'rejected', 'unsupported', 'conflict', 'failed_before_apply', 'expired'].includes(task.execution.state)
            const canApply = task.authorizationStatus === 'approved' && task.managedTarget &&
              (!task.execution || ['queued', 'preflight', 'dry_run', 'ready', 'expired', 'cancelled'].includes(task.execution.state))
            const executionLabel: Record<string, string> = {
              queued: 'Запрос публикации сохранён. Подготовка ещё не началась.',
              preflight: 'Проверяется подключение CMS и точное состояние страницы. Запись ещё не разрешена.',
              dry_run: 'Проверяется команда без записи изменений.',
              unsupported: 'Автопубликация этой версии не поддерживается. Команда записи не отправлена.',
              conflict: 'Состояние страницы изменилось до публикации. Команда записи не отправлена.',
              failed_before_apply: 'Подготовка остановлена до отправки команды записи.',
              rejected: 'Публикация отклонена: одобрение или права изменились. Команда записи не отправлена.',
              ready: 'Готово к публикации', applying: 'Результат публикации уточняется',
              outcome_unknown: 'Результат неизвестен — требуется проверка статуса',
              needs_reconciliation: 'Автоматическая сверка остановлена. Требуется ручной разбор; повторная публикация заблокирована.',
              applied_unverified: 'Изменения записаны. Независимая проверка ещё не выполнена.',
              no_op: 'Изменения не потребовались. Независимая проверка ещё не выполнена.', cancelled: 'Операция отменена',
              expired: 'Время подготовки публикации истекло. Можно подготовить новую операцию.',
            }
            const recoveryReason: Record<string, string> = {
              cms_unavailable: 'CMS недоступна', operation_not_found: 'CMS пока не подтвердила операцию',
              receipt_invalid: 'Подтверждение CMS не прошло проверку', integration_changed: 'Подключение CMS изменилось',
              lease_expired: 'Процесс сверки был прерван', attempts_exhausted: 'Лимит попыток исчерпан',
              deadline_exceeded: 'Время автоматической сверки истекло',
              cms_precondition_conflict: 'Состояние страницы не совпадает с одобренной версией',
              cms_unsupported: 'Возможности CMS не поддерживают эту команду',
              cms_contract_invalid: 'Ответ CMS не прошёл проверку', authorization_expired: 'Срок разрешения записи истёк',
              authorization_rejected: 'Одобрение или права изменились', preparation_expired: 'Лимит подготовки исчерпан',
              authority_unavailable: 'Сервис согласования недоступен или вернул некорректное разрешение',
            }

            return (
              <Card key={task.id} className="overflow-hidden p-0">
                <div className="grid gap-0 xl:grid-cols-[360px_1fr]">
                  <aside className="border-b border-gray-200 bg-gray-50 p-6 xl:border-b-0 xl:border-r">
                    <div className="mb-4 flex items-center justify-between gap-3">
                      <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-700">
                        {task.task?.taskType || 'согласование'}
                      </span>
                      <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
                        Влияние {formatImpact(task.impactScore)}
                      </span>
                    </div>

                    <h2 className="text-xl font-bold text-gray-900">{getTaskTitle(task)}</h2>
                    <p className="mt-3 text-sm leading-6 text-gray-600">{getTaskDescription(task)}</p>

                    <div className="mt-5 space-y-3 rounded-xl border border-gray-200 bg-white p-4 text-sm">
                      <div>
                        <p className="text-xs uppercase tracking-wide text-gray-400">Проект</p>
                        <p className="mt-1 break-all font-medium text-gray-900">{task.projectId}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wide text-gray-400">URL</p>
                        <p className="mt-1 break-all font-medium text-gray-900">{getReadableMeta(taskUrl)}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wide text-gray-400">Идентификатор корреляции</p>
                        <p className="mt-1 break-all font-medium text-gray-900">
                          {getReadableMeta(task.metadata.correlation_id || task.metadata.correlationId)}
                        </p>
                      </div>
                    </div>

                    <label htmlFor={`hitl-comment-${task.taskId}`} className="mt-5 block text-sm font-medium text-gray-700">
                      Комментарий к решению
                    </label>
                    <textarea
                      id={`hitl-comment-${task.taskId}`}
                      value={comments[task.taskId] ?? ''}
                      onChange={(event) =>
                        setComments((current) => ({
                          ...current,
                          [task.taskId]: event.target.value,
                        }))
                      }
                      className="mt-2 min-h-24 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                      placeholder="Например: правку можно публиковать после проверки title."
                    />

                    <div className="mt-4 grid grid-cols-2 gap-3">
                      <Button type="button" variant="outline" disabled={isProcessing || !canDecide} onClick={() => void handleDecision(task, false)}>
                        Отклонить
                      </Button>
                      <Button type="button" disabled={isProcessing || !canDecide} onClick={() => void handleDecision(task, true)}>
                        Одобрить
                      </Button>
                    </div>
                    <p className="mt-2 text-sm text-gray-600">Одобрение сохраняет решение. Публикация выполняется отдельно.</p>
                    {(canApply || reconcile) && <Button type="button" className="mt-3 w-full" disabled={isProcessing}
                      onClick={() => void handleApply(task)}>{reconcile ? 'Проверить результат публикации' : preparing ? 'Продолжить подготовку публикации' : 'Применить одобренные изменения'}</Button>}
                    {canCancel && <Button type="button" variant="outline" className="mt-3 w-full" disabled={isProcessing}
                      onClick={() => void handleCancel(task)}>Отменить операцию до публикации</Button>}
                    {task.execution && <p role="status" className="mt-2 text-sm text-gray-700">{executionLabel[task.execution.state] || 'Неизвестный статус операции'}</p>}
                    {task.execution?.preparation?.errorCode && <p className="mt-2 text-sm text-gray-600">
                      {recoveryReason[task.execution.preparation.errorCode] || 'Подготовка требует проверки'}.
                    </p>}
                    {task.execution?.recovery && <p className="mt-2 text-sm text-gray-600">
                      Попытки сверки: {task.execution.recovery.attempts} из {task.execution.recovery.maxAttempts}.
                      {task.execution.recovery.lastError && ` ${recoveryReason[task.execution.recovery.lastError] || 'Результат CMS требует проверки'}.`}
                      {task.execution.recovery.stopReason && ` ${recoveryReason[task.execution.recovery.stopReason] || 'Автоматическая сверка остановлена'}.`}
                    </p>}
                    {task.authorizationStatus === 'approved' && !task.managedTarget && <p className="mt-2 text-sm text-amber-700">Для этой версии доступна ручная публикация.</p>}
                    {!canDecide && <p className="mt-2 text-sm text-amber-700">Для решения требуется актуальная проверенная версия.</p>}
                  </aside>

                  <section className="min-w-0 p-6">
                    <div className="mb-4 flex items-center justify-between gap-4">
                      <div>
                        <h3 className="text-lg font-semibold text-gray-900">Просмотр изменений</h3>
                        <p className="mt-1 text-sm text-gray-600">Сравнение текущего состояния и предлагаемого изменения.</p>
                      </div>
                      <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium uppercase tracking-wide text-gray-600">
                        {task.status}
                      </span>
                    </div>

                    <div className="overflow-hidden rounded-xl border border-gray-200">
                      <ReactDiffViewer
                        oldValue={formatJson(task.diffData.before)}
                        newValue={formatJson(task.diffData.after)}
                        splitView
                        compareMethod={DiffMethod.WORDS}
                        leftTitle="До"
                        rightTitle="После"
                        showDiffOnly={false}
                        useDarkTheme={false}
                      />
                    </div>
                  </section>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default HITL
