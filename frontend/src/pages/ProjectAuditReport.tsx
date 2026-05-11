import { useEffect, useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { pollAuditStatus, setPolling } from '../store/slices/auditSlice'
import type { AuditCriterion, AuditFinding } from '../types/audit'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'

const severityRank: Record<string, number> = {
  critical: 5,
  high: 4,
  error: 4,
  medium: 3,
  warning: 3,
  low: 2,
  info: 1,
  success: 0,
}

const scoreTone = (score: number) => {
  if (score >= 80) {
    return {
      text: 'text-emerald-700',
      bar: 'bg-emerald-600',
      border: 'border-emerald-200',
      bg: 'bg-emerald-50',
      label: 'Хорошо',
    }
  }
  if (score >= 50) {
    return {
      text: 'text-amber-700',
      bar: 'bg-amber-500',
      border: 'border-amber-200',
      bg: 'bg-amber-50',
      label: 'Нужны правки',
    }
  }
  return {
    text: 'text-red-700',
    bar: 'bg-red-600',
    border: 'border-red-200',
    bg: 'bg-red-50',
    label: 'Критично',
  }
}

const findingTone = (finding: AuditFinding) => {
  switch (finding.status) {
    case 'error':
      return 'border-red-200 bg-red-50 text-red-800'
    case 'warning':
      return 'border-amber-200 bg-amber-50 text-amber-800'
    case 'info':
      return 'border-blue-200 bg-blue-50 text-blue-800'
    default:
      return 'border-emerald-200 bg-emerald-50 text-emerald-800'
  }
}

const severityLabel = (severity?: string) => {
  const value = (severity || '').toLowerCase()
  if (value === 'critical') return 'Критично'
  if (value === 'high' || value === 'error') return 'Высокий риск'
  if (value === 'medium' || value === 'warning') return 'Средний риск'
  if (value === 'low') return 'Низкий риск'
  if (value === 'info') return 'Информация'
  return 'Проверено'
}

const criterionStatusLabel = (criterion: AuditCriterion) => {
  if (criterion.issue_count > 0) {
    return `${criterion.issue_count} замеч.`
  }
  if ((criterion.info_count ?? 0) > 0) {
    return 'Есть уточнения'
  }
  return 'Без замечаний'
}

const formatDetailValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') {
    return ''
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const flattenCriterionFindings = (criteria: AuditCriterion[]) =>
  criteria.flatMap((criterion) => criterion.findings)

const getTopFindings = (findings: AuditFinding[]) =>
  [...findings]
    .sort((left, right) => {
      const leftRank = severityRank[(left.severity || left.status || '').toLowerCase()] ?? 0
      const rightRank = severityRank[(right.severity || right.status || '').toLowerCase()] ?? 0
      return rightRank - leftRank
    })
    .slice(0, 5)

const FindingRow = ({ finding }: { finding: AuditFinding }) => {
  const detailEntries = Object.entries(finding.details ?? {})
    .map(([key, value]) => [key, formatDetailValue(value)] as const)
    .filter(([, value]) => Boolean(value))

  return (
    <div className={`rounded-md border p-4 ${findingTone(finding)}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h4 className="font-semibold leading-6">{finding.title}</h4>
          <p className="mt-1 text-sm leading-6">{finding.description}</p>
        </div>
        <span className="shrink-0 rounded-md bg-white/80 px-2.5 py-1 text-xs font-medium">
          {severityLabel(finding.severity || finding.status)}
        </span>
      </div>
      {detailEntries.length > 0 && (
        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
          {detailEntries.map(([key, value]) => (
            <div key={key} className="rounded bg-white/70 px-2.5 py-2">
              <dt className="font-medium uppercase text-gray-500">{key}</dt>
              <dd className="mt-1 break-all text-gray-800">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {finding.recommendation && (
        <p className="mt-3 text-sm font-medium">Что сделать: {finding.recommendation}</p>
      )}
    </div>
  )
}

const ProjectAuditReport = () => {
  const { projectId, uid } = useParams<{ projectId: string; uid: string }>()
  const dispatch = useAppDispatch()
  const { currentAudit, polling, error } = useAppSelector((state) => state.audit)
  const [expandedCriterion, setExpandedCriterion] = useState<string | null>(null)

  useEffect(() => {
    if (!uid) {
      return
    }

    void dispatch(pollAuditStatus(uid)).then((result) => {
      if (pollAuditStatus.fulfilled.match(result)) {
        const status = result.payload.status
        dispatch(setPolling(status !== 'completed' && status !== 'failed'))
      }
    })
  }, [dispatch, uid])

  useEffect(() => {
    const status = currentAudit?.status
    const shouldPoll = Boolean(uid && polling && status !== 'completed' && status !== 'failed')

    if (!shouldPoll || !uid) {
      return undefined
    }

    const timer = window.setInterval(() => {
      void dispatch(pollAuditStatus(uid))
    }, 2500)

    return () => {
      window.clearInterval(timer)
    }
  }, [dispatch, polling, currentAudit?.status, uid])

  const audit = currentAudit?.uid === uid ? currentAudit : null
  const criteria = useMemo(() => audit?.summary?.criteria ?? [], [audit?.summary?.criteria])
  const allFindings = criteria.length > 0 ? flattenCriterionFindings(criteria) : audit?.details ?? []
  const topFindings = useMemo(() => getTopFindings(allFindings), [allFindings])
  const verdict = audit ? scoreTone(audit.score) : scoreTone(0)
  const projectPath = projectId ? `/dashboard/projects/${projectId}?tab=audit` : '/dashboard/projects'

  useEffect(() => {
    if (criteria.length > 0 && !expandedCriterion) {
      setExpandedCriterion(criteria[0].key)
    }
  }, [criteria, expandedCriterion])

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <Link to={projectPath} className="text-sm font-medium text-blue-700 hover:text-blue-800">
            Назад к проекту
          </Link>
          <h1 className="mt-3 text-3xl font-bold text-gray-900">Отчет расширенного аудита</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
            Результат проверки сохраненного проекта. Отчет открыт внутри личного кабинета, чтобы сохранить контекст проекта.
          </p>
        </div>
        <Link to={projectPath}>
          <Button type="button" variant="outline">К аудиту проекта</Button>
        </Link>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {!audit && !error && (
        <Card className="p-8 text-center">
          <Loader />
          <h2 className="mt-5 text-lg font-semibold text-gray-900">Загружаем отчет</h2>
          <p className="mt-2 text-sm text-gray-600">Получаем статус и результаты расширенного аудита.</p>
        </Card>
      )}

      {audit && audit.status !== 'completed' && audit.status !== 'failed' && (
        <Card className="p-8 text-center">
          <Loader />
          <h2 className="mt-5 text-lg font-semibold text-gray-900">Аудит выполняется</h2>
          <p className="mt-2 text-sm text-gray-600">Статус: {audit.status}</p>
        </Card>
      )}

      {audit?.status === 'failed' && (
        <Card className="border-red-200 bg-red-50 p-6">
          <h2 className="text-xl font-semibold text-red-900">Аудит завершился с ошибкой</h2>
          <p className="mt-2 text-sm text-red-700">{audit.error || 'Попробуйте запустить проверку еще раз.'}</p>
        </Card>
      )}

      {audit?.status === 'completed' && (
        <>
          <Card className="overflow-hidden p-0">
            <div className="grid gap-0 lg:grid-cols-[1fr_280px]">
              <div className="p-6">
                <p className="text-sm font-medium uppercase tracking-wide text-blue-700">Результат аудита</p>
                <h2 className="mt-2 text-2xl font-bold text-gray-950">Вердикт по сайту</h2>
                <p className="mt-1 break-all text-sm text-gray-600">{audit.url}</p>
                {audit.summary?.score_explanation && (
                  <p className="mt-4 max-w-3xl text-sm leading-6 text-gray-600">
                    {audit.summary.score_explanation}
                  </p>
                )}
              </div>

              <div className="bg-gray-950 p-6 text-white">
                <p className="text-sm text-gray-300">Итоговая оценка</p>
                <p className={`mt-2 text-5xl font-black ${verdict.text}`}>{audit.score}</p>
                <p className="mt-2 text-sm text-gray-300">{verdict.label}</p>
                <div className="mt-4 h-3 rounded-full bg-white/20">
                  <div className={`h-3 rounded-full ${verdict.bar}`} style={{ width: `${audit.score}%` }} />
                </div>
              </div>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <Card className="p-5">
              <p className="text-xs uppercase tracking-wide text-gray-400">Оценка</p>
              <p className="mt-2 text-3xl font-bold text-gray-900">{audit.score}/100</p>
            </Card>
            <Card className="p-5">
              <p className="text-xs uppercase tracking-wide text-gray-400">Ошибки</p>
              <p className="mt-2 text-3xl font-bold text-red-700">{audit.issues.errors}</p>
            </Card>
            <Card className="p-5">
              <p className="text-xs uppercase tracking-wide text-gray-400">Предупреждения</p>
              <p className="mt-2 text-3xl font-bold text-amber-700">{audit.issues.warnings}</p>
            </Card>
            <Card className="p-5">
              <p className="text-xs uppercase tracking-wide text-gray-400">Страницы</p>
              <p className="mt-2 text-3xl font-bold text-gray-900">{audit.pages?.length ?? 0}</p>
            </Card>
          </div>

          {criteria.length > 0 && (
            <Card className="p-6">
              <div className="mb-5">
                <h3 className="text-xl font-bold text-gray-950">Оценка по критериям</h3>
                <p className="mt-1 text-sm text-gray-600">
                  Раскройте критерий, чтобы увидеть конкретные ошибки, недочеты и успешно пройденные проверки.
                </p>
              </div>

              <div className="space-y-3">
                {criteria.map((criterion) => {
                  const tone = scoreTone(criterion.score)
                  const expanded = expandedCriterion === criterion.key

                  return (
                    <div key={criterion.key} className={`rounded-lg border ${tone.border} bg-white`}>
                      <button
                        type="button"
                        onClick={() => setExpandedCriterion(expanded ? null : criterion.key)}
                        className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left"
                      >
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <h4 className="font-semibold text-gray-950">{criterion.title}</h4>
                            <span className={`rounded-md px-2 py-1 text-xs font-medium ${tone.bg} ${tone.text}`}>
                              {criterionStatusLabel(criterion)}
                            </span>
                          </div>
                          <p className="mt-1 text-sm leading-6 text-gray-600">{criterion.description}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          <span className={`text-lg font-bold ${tone.text}`}>{criterion.score}</span>
                          <ChevronDown className={`h-5 w-5 text-gray-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                        </div>
                      </button>

                      {expanded && (
                        <div className="border-t border-gray-200 px-4 py-4">
                          {criterion.findings.length > 0 ? (
                            <div className="space-y-3">
                              {criterion.findings.map((finding, index) => (
                                <FindingRow key={`${criterion.key}-${finding.code}-${index}`} finding={finding} />
                              ))}
                            </div>
                          ) : (
                            <div className="rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
                              По этому критерию явных проблем не найдено.
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </Card>
          )}

          <Card className="p-6">
            <h3 className="text-xl font-bold text-gray-950">Главные замечания</h3>
            <p className="mt-1 text-sm text-gray-600">Наиболее важные проблемы, отсортированные по серьезности.</p>

            {topFindings.length > 0 ? (
              <div className="mt-5 space-y-3">
                {topFindings.map((finding, index) => (
                  <FindingRow key={`${finding.code}-${index}`} finding={finding} />
                ))}
              </div>
            ) : (
              <p className="mt-5 rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
                Значимых проблем не найдено.
              </p>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

export default ProjectAuditReport
