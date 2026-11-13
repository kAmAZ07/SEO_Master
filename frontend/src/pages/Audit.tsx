import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ChevronDown, Download } from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { cancelAuditRequest, resetAudit, startAudit } from '../store/slices/auditSlice'
import { useAuditPolling } from '../hooks/useAuditPolling'
import { useProjectSave } from '../hooks/useProjectSave'
import type { AuditCriterion, AuditFinding } from '../types/audit'
import { describeAuditScore, hasAuditResults, isActiveAuditStatus } from '../utils/auditStatus'
import AuditStatePanel from '../components/audit/AuditStatePanel'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'

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

const scoreTone = (score: number | null) => {
  if (score === null) {
    return {
      text: 'text-gray-300',
      bar: 'bg-gray-500',
      border: 'border-gray-200',
      bg: 'bg-gray-50',
      label: 'Оценка недоступна',
    }
  }
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

const getTopFindings = (findings: AuditFinding[]) =>
  [...findings]
    .sort((left, right) => {
      const leftRank = severityRank[(left.severity || left.status || '').toLowerCase()] ?? 0
      const rightRank = severityRank[(right.severity || right.status || '').toLowerCase()] ?? 0
      return rightRank - leftRank
    })
    .slice(0, 5)

const flattenCriterionFindings = (criteria: AuditCriterion[]) =>
  criteria.flatMap((criterion) => criterion.findings)

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

const escapeCsvCell = (value: unknown): string => {
  const str = value === null || value === undefined ? '' : String(value)
  return `"${str.replace(/"/g, '""')}"`
}

const exportAuditToCsv = (audit: import('../types/audit').AuditStatus) => {
  const rows: string[][] = []

  rows.push(['SEO Master — Отчёт аудита'])
  rows.push(['URL', audit.url])
  rows.push(['Оценка', describeAuditScore(audit.score)])
  rows.push(['Дата', new Date(audit.createdAt).toLocaleString('ru-RU')])
  rows.push([])
  rows.push(['Критерий', 'Код проверки', 'Название замечания', 'Описание', 'Серьёзность', 'Статус', 'Рекомендация'])

  const criteria = audit.summary?.criteria ?? []
  if (criteria.length > 0) {
    for (const criterion of criteria) {
      if (criterion.findings.length === 0) {
        rows.push([criterion.title, '', 'Без замечаний', criterion.description, '', 'success', ''])
      } else {
        for (const finding of criterion.findings) {
          rows.push([
            criterion.title,
            finding.code,
            finding.title,
            finding.description,
            severityLabel(finding.severity || finding.status),
            finding.status,
            finding.recommendation ?? '',
          ])
        }
      }
    }
  } else {
    for (const finding of audit.details) {
      rows.push([
        finding.category ?? '',
        finding.code,
        finding.title,
        finding.description,
        severityLabel(finding.severity || finding.status),
        finding.status,
        finding.recommendation ?? '',
      ])
    }
  }

  const csv = '﻿' + rows.map((row) => row.map(escapeCsvCell).join(',')).join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = `audit-${audit.uid}.csv`
  anchor.click()
  URL.revokeObjectURL(objectUrl)
}

const Audit = () => {
  const { uid } = useParams<{ uid?: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const dispatch = useAppDispatch()
  const { currentAudit: storedAudit, loading, cancelling, error, errorKind } = useAppSelector((state) => state.audit)
  const { sessionStatus } = useAppSelector((state) => state.auth)
  const [url, setUrl] = useState('')
  const [expandedCriterion, setExpandedCriterion] = useState<string | null>(null)
  const projectId = searchParams.get('project') ?? undefined
  const shouldSaveProject = searchParams.get('saveProject') === '1'
  const isAuthenticated = sessionStatus === 'authenticated'
  // Пока профиль подтверждается сервером, решение «вошёл ли пользователь» не принимается.
  const sessionRestoring = sessionStatus === 'restoring'
  const isProjectAudit = Boolean(projectId && isAuthenticated)

  // На маршруте без uid прежний результат не показывается; на маршруте с uid — только результат этого uid.
  const currentAudit = storedAudit && (!uid || storedAudit.uid === uid) ? storedAudit : null

  useEffect(() => {
    if (!uid) {
      dispatch(resetAudit())
    }
  }, [dispatch, uid])

  const { gaveUp, retry: retryPolling } = useAuditPolling(uid)
  const { state: saveState, requestSave, retry: retrySave } = useProjectSave({
    audit: currentAudit,
    canSave: isAuthenticated,
    autoSave: shouldSaveProject,
  })
  const savingProject = saveState.status === 'saving'

  const criteria = useMemo(() => currentAudit?.summary?.criteria ?? [], [currentAudit?.summary?.criteria])

  useEffect(() => {
    if (criteria.length > 0 && !expandedCriterion) {
      setExpandedCriterion(criteria[0].key)
    }
  }, [criteria, expandedCriterion])

  const handleStartAudit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const audit = await dispatch(startAudit({ url, projectId })).unwrap()
      setUrl('')

      if (audit.uid) {
        navigate(projectId ? `/audit/results/${audit.uid}?project=${projectId}` : `/audit/results/${audit.uid}`)
      }
    } catch {
      // Ошибка запуска уже сохранена в состоянии аудита (rejected-действие).
    }
  }

  const handleSaveProject = () => {
    if (!currentAudit?.uid) {
      return
    }
    // Не вошёл — запоминаем намерение и ведём на регистрацию; после входа сохранение выполнится один раз само.
    if (!requestSave() && !isAuthenticated && !sessionRestoring) {
      navigate(`/register?redirect=${encodeURIComponent(`/audit/results/${currentAudit.uid}?saveProject=1`)}`)
    }
  }

  const allFindings = useMemo(
    () => (criteria.length > 0 ? flattenCriterionFindings(criteria) : currentAudit?.details ?? []),
    [criteria, currentAudit?.details],
  )
  const topFindings = useMemo(() => getTopFindings(allFindings), [allFindings])
  const showResults = currentAudit && hasAuditResults(currentAudit.status)
  const verdict = scoreTone(currentAudit ? currentAudit.score : null)
  // Отмена есть только у публичного аудита: проектный запускается через другой маршрут.
  const canCancel = Boolean(uid && !projectId && currentAudit && isActiveAuditStatus(currentAudit.status))

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
        <Link to="/" className="text-lg font-bold tracking-tight text-gray-950">
          SEO Master
        </Link>
        <div className="flex items-center gap-3">
          {isAuthenticated ? (
            <Link to="/dashboard">
              <Button size="sm">Личный кабинет</Button>
            </Link>
          ) : (
            <>
              <Link to="/login" className="text-sm font-medium text-gray-700 transition-colors hover:text-blue-700">
                Войти
              </Link>
              <Link to="/register">
                <Button size="sm">Создать аккаунт</Button>
              </Link>
            </>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 pb-16">
        <section className="grid items-start gap-8 py-8 lg:grid-cols-[1fr_420px] lg:py-12">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">
              {isProjectAudit ? 'Расширенный SEO-аудит проекта' : 'Публичный SEO-аудит'}
            </p>
            <h1 className="mt-3 max-w-3xl text-4xl font-black tracking-tight text-gray-950 md:text-5xl">
              {isProjectAudit ? 'Глубокая проверка сайта в личном кабинете' : 'Проверка сайта по понятным критериям без регистрации'}
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-gray-700">
              {isProjectAudit
                ? 'Расширенный аудит проходит по сохраненному проекту: увеличивает глубину обхода, включает JS-rendering и дополнительные проектные данные, когда они доступны.'
                : 'Аудит проверяет техническую доступность, метаданные, структуру контента, schema.org, внутренние ссылки и базовые performance-сигналы. Для каждого блока показывается отдельная оценка и список найденных замечаний.'}
            </p>
          </div>

          <Card id="quick-audit" className="rounded-lg p-6">
            <h2 className="text-xl font-bold text-gray-950">{isProjectAudit ? 'Запустить аудит проекта' : 'Проверить сайт'}</h2>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              {isProjectAudit
                ? 'Введите URL для проверки или оставьте поле пустым, чтобы использовать адрес из карточки проекта.'
                : 'Введите публичный URL. Локальные и приватные адреса не принимаются.'}
            </p>
            <form onSubmit={handleStartAudit} className="mt-6 space-y-4">
              <Input
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder={isProjectAudit ? 'Оставьте пустым для URL проекта' : 'https://example.com'}
                label="URL сайта"
                required={!isProjectAudit}
              />
              <Button type="submit" disabled={loading} className="w-full">
                {loading ? 'Запускаем аудит...' : isProjectAudit ? 'Запустить расширенный аудит' : 'Получить результат'}
              </Button>
            </form>
          </Card>
        </section>

        <div className="mt-2">
          <AuditStatePanel
            audit={currentAudit}
            error={error}
            errorKind={errorKind}
            gaveUp={gaveUp}
            onRetry={retryPolling}
            onCancel={canCancel && uid ? () => void dispatch(cancelAuditRequest(uid)) : undefined}
            cancelling={cancelling}
          />
        </div>

        {showResults && (
          <section className="mt-8 space-y-6">
            <div className="flex justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={() => exportAuditToCsv(currentAudit!)}
              >
                <Download className="mr-2 h-4 w-4" />
                Скачать CSV
              </Button>
            </div>

            <Card className="overflow-hidden rounded-lg p-0">
              <div className="grid gap-0 lg:grid-cols-[1fr_280px]">
                <div className="p-6">
                  <p className="text-sm font-medium uppercase tracking-wide text-blue-700">Результат аудита</p>
                  <h2 className="mt-2 text-2xl font-bold text-gray-950">Вердикт по сайту</h2>
                  <p className="mt-1 break-all text-sm text-gray-600">{currentAudit.url}</p>
                  {currentAudit.summary?.score_explanation && (
                    <p className="mt-4 max-w-3xl text-sm leading-6 text-gray-600">
                      {currentAudit.summary.score_explanation}
                    </p>
                  )}
                </div>

                <div className="bg-gray-950 p-6 text-white">
                  <p className="text-sm text-gray-300">Итоговая оценка (эвристика)</p>
                  <p className={`mt-2 text-5xl font-black ${verdict.text}`}>
                    {currentAudit.score === null ? '—' : currentAudit.score}
                  </p>
                  <p className="mt-2 text-sm text-gray-300">{verdict.label}</p>
                  <div className="mt-4 h-3 rounded-full bg-white/20">
                    <div className={`h-3 rounded-full ${verdict.bar}`} style={{ width: `${currentAudit.score ?? 0}%` }} />
                  </div>
                  <p className="mt-3 text-xs text-gray-400">
                    Оценка рассчитана по эвристике и показывает приоритет правок, а не измеренный результат.
                  </p>
                </div>
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Card className="rounded-lg p-5">
                <p className="text-3xl font-bold text-emerald-700">{criteria.filter((item) => item.issue_count === 0).length}</p>
                <p className="mt-1 text-sm text-gray-600">Критериев без проблем</p>
              </Card>
              <Card className="rounded-lg p-5">
                <p className="text-3xl font-bold text-amber-700">{currentAudit.issues.warnings}</p>
                <p className="mt-1 text-sm text-gray-600">Предупреждений</p>
              </Card>
              <Card className="rounded-lg p-5">
                <p className="text-3xl font-bold text-red-700">{currentAudit.issues.errors}</p>
                <p className="mt-1 text-sm text-gray-600">Серьезных ошибок</p>
              </Card>
            </div>

            {criteria.length > 0 && (
              <Card className="rounded-lg p-6">
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
                          aria-expanded={expanded}
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

                            {(criterion.checked?.length ?? 0) > 0 && (
                              <div className="mt-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Проверялось</p>
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {criterion.checked?.map((item) => (
                                    <span key={item} className="rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-700">
                                      {item}
                                    </span>
                                  ))}
                                </div>
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

            <Card className="rounded-lg p-6">
              <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
                <div>
                  <h3 className="text-xl font-bold text-gray-950">Главные замечания</h3>
                  <p className="mt-1 text-sm text-gray-600">
                    Краткий список наиболее важных проблем, отсортированный по серьезности.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleSaveProject}
                  disabled={savingProject || sessionRestoring}
                >
                  {savingProject ? 'Сохраняем проект...' : 'Сохранить проект в кабинете'}
                </Button>
              </div>

              {saveState.status === 'failed' && (
                <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  <p>{saveState.message}</p>
                  <Button type="button" size="sm" variant="outline" className="mt-3" onClick={retrySave}>
                    Повторить сохранение
                  </Button>
                </div>
              )}

              {topFindings.length > 0 ? (
                <div className="space-y-3">
                  {topFindings.map((finding, index) => (
                    <FindingRow key={`${finding.code}-${index}`} finding={finding} />
                  ))}
                </div>
              ) : (
                <p className="rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
                  Значимых публичных проблем не найдено. Для истории проверок, приватных задач и внедрения правок нужен личный кабинет.
                </p>
              )}
            </Card>
          </section>
        )}
      </main>
    </div>
  )
}

export default Audit
