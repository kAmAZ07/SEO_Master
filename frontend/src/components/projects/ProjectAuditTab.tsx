import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../../api/axiosConfig'
import { useAuditPolling } from '../../hooks/useAuditPolling'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { resetAudit, startAudit } from '../../store/slices/auditSlice'
import { describeAuditScore, formatAuditStatus, hasAuditResults, isTerminalAuditStatus } from '../../utils/auditStatus'
import AuditStatePanel from '../audit/AuditStatePanel'
import Card from '../ui/Card'
import Button from '../ui/Button'
import Input from '../ui/Input'
import Loader from '../ui/Loader'

interface ProjectAuditTabProps {
  projectId: string
  projectUrl: string
  // Проект без корректной области (scope_review): расширенный аудит недоступен.
  scopeReview?: boolean
  // Перейти к блоку подтверждения владения (вкладка «Обзор»).
  onOpenOwnership?: () => void
}

interface AuditHistoryItem {
  id: string
  uid: string
  url: string
  status: string
  score: number | null
  createdAt: string | null
  mode: string | null
}

const ProjectAuditTab = ({ projectId, projectUrl, scopeReview = false, onOpenOwnership }: ProjectAuditTabProps) => {
  const dispatch = useAppDispatch()
  const { currentAudit, loading, error, errorKind, errorCode } = useAppSelector((state) => state.audit)
  const [url, setUrl] = useState(projectUrl)
  const [auditUid, setAuditUid] = useState<string | null>(null)
  const [history, setHistory] = useState<AuditHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const { gaveUp, retry } = useAuditPolling(auditUid ?? undefined)

  const loadHistory = useCallback(
    async (isCurrent: () => boolean = () => true) => {
      setHistoryLoading(true)
      setHistoryError(null)
      try {
        const response = await api.get<AuditHistoryItem[]>('/audit/history', { params: { projectId } })
        if (isCurrent()) {
          setHistory(Array.isArray(response.data) ? response.data : [])
        }
      } catch {
        if (isCurrent()) {
          setHistoryError('Не удалось загрузить историю аудитов.')
        }
      } finally {
        if (isCurrent()) {
          setHistoryLoading(false)
        }
      }
    },
    [projectId],
  )

  useEffect(() => {
    setUrl(projectUrl)
  }, [projectUrl])

  // Вкладка открывается без чужого результата и чужой ошибки от предыдущего экрана.
  useEffect(() => {
    dispatch(resetAudit())
  }, [dispatch])

  useEffect(() => {
    // Ответ по предыдущему проекту (или после размонтирования) в историю не попадает.
    let current = true
    void loadHistory(() => current)
    return () => {
      current = false
    }
  }, [loadHistory])

  const finishedStatus = currentAudit && isTerminalAuditStatus(currentAudit.status) ? currentAudit.status : null
  useEffect(() => {
    if (finishedStatus) {
      void loadHistory()
    }
  }, [finishedStatus, loadHistory])

  const activeAudit = useMemo(() => {
    if (!currentAudit || (auditUid && currentAudit.uid !== auditUid)) {
      return null
    }
    return currentAudit
  }, [auditUid, currentAudit])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const audit = await dispatch(startAudit({ projectId, url: url.trim() || projectUrl })).unwrap()
      setAuditUid(audit.uid)
    } catch {
      // Ошибка запуска уже сохранена в состоянии аудита (rejected-действие).
    }
  }

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Расширенный аудит проекта</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
              Этот режим доступен для сохраненного проекта: обходит больше страниц, использует большую глубину проверки,
              включает JS-rendering и подключает проектные данные, когда они доступны.
            </p>
          </div>
          {activeAudit?.uid && (
            <Link to={`/dashboard/projects/${projectId}/audits/${activeAudit.uid}`}>
              <Button type="button" variant="outline">Открыть полный отчет</Button>
            </Link>
          )}
        </div>

        <form onSubmit={handleSubmit} className="mt-6 grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
          <Input
            label="URL для проверки"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder={projectUrl}
          />
          <Button type="submit" disabled={loading || scopeReview}>
            {loading ? 'Запускаем...' : 'Запустить аудит'}
          </Button>
        </form>

        {scopeReview && (
          <p role="status" className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            У проекта нет корректной области сайта, поэтому расширенный аудит недоступен.
          </p>
        )}

        {errorCode === 'domain_not_verified' && onOpenOwnership && (
          <div className="mt-3">
            <Button type="button" variant="outline" onClick={onOpenOwnership}>
              Перейти к подтверждению владения
            </Button>
          </div>
        )}
      </Card>

      <AuditStatePanel
        audit={activeAudit}
        error={error}
        errorKind={errorKind}
        gaveUp={gaveUp}
        onRetry={retry}
      />

      {activeAudit && hasAuditResults(activeAudit.status) && (
        <Card className="p-6">
          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Оценка</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{describeAuditScore(activeAudit.score)}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Ошибки</p>
              <p className="mt-2 text-3xl font-bold text-red-700">{activeAudit.issues.errors}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Предупреждения</p>
              <p className="mt-2 text-3xl font-bold text-amber-700">{activeAudit.issues.warnings}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Страницы</p>
              <p className="mt-2 text-3xl font-bold text-gray-900">{activeAudit.pages?.length ?? 0}</p>
            </div>
          </div>
        </Card>
      )}

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 className="text-xl font-semibold text-gray-900">История аудитов</h2>
          <Button type="button" variant="outline" onClick={() => void loadHistory()} disabled={historyLoading}>
            Обновить
          </Button>
        </div>

        {historyError && (
          <p role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {historyError}
          </p>
        )}

        {historyLoading && !history.length ? (
          <Loader />
        ) : history.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr className="border-b border-gray-200 text-left text-sm text-gray-500">
                  <th className="px-4 py-3 font-medium">URL</th>
                  <th className="px-4 py-3 font-medium">Статус</th>
                  <th className="px-4 py-3 font-medium">Оценка</th>
                  <th className="px-4 py-3 font-medium">Дата</th>
                  <th className="px-4 py-3 font-medium">Отчёт</th>
                </tr>
              </thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.id} className="border-b border-gray-100 text-sm text-gray-700">
                    <td className="max-w-[260px] truncate px-4 py-3 font-medium text-gray-900">{item.url}</td>
                    <td className="px-4 py-3">{formatAuditStatus(item.status)}</td>
                    <td className="px-4 py-3">{item.score != null ? describeAuditScore(item.score) : '—'}</td>
                    <td className="px-4 py-3">
                      {item.createdAt ? new Date(item.createdAt).toLocaleString('ru-RU') : '—'}
                    </td>
                    <td className="px-4 py-3">
                      {item.status === 'completed' || item.status === 'partial' ? (
                        <Link
                          to={`/dashboard/projects/${projectId}/audits/${item.uid}`}
                          className="font-medium text-blue-600 hover:text-blue-700"
                        >
                          Открыть
                        </Link>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Аудиты ещё не запускались для этого проекта.</p>
        )}
      </Card>
    </div>
  )
}

export default ProjectAuditTab
