import { useEffect, useMemo, useState } from 'react'
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'
import { approveHITLTask, loadHITLTasks, rejectHITLTask } from '../store/slices/hitlSlice'
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

  useEffect(() => {
    void dispatch(loadHITLTasks())
  }, [dispatch])

  const sortedTasks = useMemo(
    () => [...tasks].sort((left, right) => (right.impactScore ?? 0) - (left.impactScore ?? 0)),
    [tasks],
  )

  const handleDecision = async (task: HITLTask, approved: boolean) => {
    const comment = comments[task.taskId]?.trim()
    setProcessingTaskId(task.taskId)

    try {
      if (approved) {
        await dispatch(approveHITLTask({ taskId: task.taskId, approved: true, comment })).unwrap()
      } else {
        await dispatch(rejectHITLTask({ taskId: task.taskId, approved: false, comment })).unwrap()
      }
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
                      <Button type="button" variant="outline" disabled={isProcessing} onClick={() => void handleDecision(task, false)}>
                        Отклонить
                      </Button>
                      <Button type="button" disabled={isProcessing} onClick={() => void handleDecision(task, true)}>
                        Одобрить
                      </Button>
                    </div>
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
