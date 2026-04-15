import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { pollAuditStatus, setPolling, startAudit } from '../store/slices/auditSlice'
import type { AuditFinding } from '../types/audit'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

const severityBadgeClass = (detail: AuditFinding) => {
  switch (detail.status) {
    case 'error':
      return 'border-red-200 bg-red-50 text-red-700'
    case 'warning':
      return 'border-amber-200 bg-amber-50 text-amber-700'
    case 'info':
      return 'border-blue-200 bg-blue-50 text-blue-700'
    default:
      return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  }
}

const scoreColorClass = (score: number) => {
  if (score >= 80) {
    return 'text-emerald-600'
  }
  if (score >= 50) {
    return 'text-amber-600'
  }
  return 'text-red-600'
}

const progressColorClass = (score: number) => {
  if (score >= 80) {
    return 'bg-emerald-600'
  }
  if (score >= 50) {
    return 'bg-amber-500'
  }
  return 'bg-red-600'
}

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

const getTopFindings = (findings: AuditFinding[]) =>
  [...findings]
    .sort((left, right) => {
      const leftRank = severityRank[(left.severity || left.status || '').toLowerCase()] ?? 0
      const rightRank = severityRank[(right.severity || right.status || '').toLowerCase()] ?? 0
      return rightRank - leftRank
    })
    .slice(0, 5)

const Audit = () => {
  const { uid } = useParams<{ uid?: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const dispatch = useAppDispatch()
  const { currentAudit, loading, polling, error } = useAppSelector((state) => state.audit)
  const [url, setUrl] = useState('')
  const projectId = searchParams.get('project') ?? undefined

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
    const activeUid = uid || currentAudit?.uid
    const status = currentAudit?.status
    const shouldPoll = Boolean(activeUid && polling && status !== 'completed' && status !== 'failed')

    if (!shouldPoll || !activeUid) {
      return undefined
    }

    const timer = window.setInterval(() => {
      void dispatch(pollAuditStatus(activeUid))
    }, 2500)

    return () => {
      window.clearInterval(timer)
    }
  }, [dispatch, polling, currentAudit?.status, currentAudit?.uid, uid])

  const handleStartAudit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const audit = await dispatch(startAudit({ url, projectId })).unwrap()
      setUrl('')

      if (audit.uid) {
        navigate(`/audit/results/${audit.uid}`)
      }
    } catch {
      // The slice stores a user-facing error; keeping the handler quiet avoids duplicate noise.
    }
  }

  const topFindings = useMemo(() => getTopFindings(currentAudit?.details ?? []), [currentAudit?.details])
  const hasResultRoute = Boolean(uid)
  const showProgress = currentAudit && currentAudit.status !== 'completed' && currentAudit.status !== 'failed'
  const showResults = currentAudit && currentAudit.status === 'completed'

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_#dbeafe,_transparent_36%),linear-gradient(135deg,_#f8fafc_0%,_#eef6ff_45%,_#fff7ed_100%)]">
      <header className="mx-auto flex max-w-7xl items-center justify-between px-6 py-6">
        <Link to="/" className="text-lg font-bold tracking-tight text-gray-950">
          SEO Master
        </Link>
        <div className="flex items-center gap-3">
          <Link to="/login" className="text-sm font-medium text-gray-700 transition-colors hover:text-blue-700">
            Войти
          </Link>
          <Link to="/register">
            <Button size="sm">Создать аккаунт</Button>
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 pb-16">
        <section className="grid items-center gap-8 py-10 lg:grid-cols-[1.05fr_0.95fr] lg:py-16">
          <div>
            <div className="mb-5 inline-flex rounded-full border border-blue-100 bg-white/70 px-4 py-2 text-sm font-medium text-blue-700 shadow-sm backdrop-blur">
              Публичный SEO-аудит без регистрации
            </div>
            <h1 className="max-w-3xl text-4xl font-black tracking-tight text-gray-950 md:text-6xl">
              Быстрая проверка сайта и понятный top-5 проблем за один запуск
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-gray-700">
              Введите URL, получите публичный результат по прямой ссылке, а для истории аудитов, HITL-согласований и внедрения правок переходите в приватный кабинет.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <a href="#quick-audit">
                <Button size="lg" className="w-full sm:w-auto">
                  Запустить quick audit
                </Button>
              </a>
              <Link to="/register">
                <Button type="button" variant="outline" size="lg" className="w-full bg-white/70 sm:w-auto">
                  Сохранить результаты в проект
                </Button>
              </Link>
            </div>
          </div>

          <Card id="quick-audit" className="border-blue-100 bg-white/85 p-6 shadow-xl shadow-blue-100/40 backdrop-blur">
            <h2 className="text-2xl font-bold text-gray-950">Проверить сайт</h2>
            <p className="mt-2 text-sm leading-6 text-gray-600">
              Public flow использует только открытый quick-audit контур и не запрашивает приватную историю проекта.
            </p>
            <form onSubmit={handleStartAudit} className="mt-6 space-y-4">
              <Input
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://example.com"
                label="URL сайта"
                required
              />
              <Button type="submit" disabled={loading} className="w-full">
                {loading ? 'Запускаем аудит...' : 'Получить публичный результат'}
              </Button>
            </form>
            {hasResultRoute && currentAudit?.uid && (
              <p className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-700">
                Прямая ссылка на результат: /audit/results/{currentAudit.uid}
              </p>
            )}
          </Card>
        </section>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {showProgress && (
          <Card className="mt-6 p-8 text-center">
            <Loader />
            <h2 className="mt-5 text-xl font-semibold text-gray-950">Аудит выполняется</h2>
            <p className="mt-2 text-gray-600">Проверяем технические сигналы, страницы, ссылки и базовые SEO-проблемы.</p>
          </Card>
        )}

        {currentAudit?.status === 'failed' && (
          <Card className="mt-6 border-red-200 bg-red-50 p-6">
            <h2 className="text-xl font-semibold text-red-900">Аудит завершился с ошибкой</h2>
            <p className="mt-2 text-sm text-red-700">{currentAudit.error || 'Попробуйте запустить проверку ещё раз.'}</p>
          </Card>
        )}

        {showResults && (
          <section className="mt-8 space-y-6">
            <Card className="overflow-hidden p-0">
              <div className="grid gap-0 lg:grid-cols-[1fr_300px]">
                <div className="p-6">
                  <p className="text-sm font-medium uppercase tracking-wide text-blue-700">Public result</p>
                  <h2 className="mt-2 text-2xl font-bold text-gray-950">Результаты quick audit</h2>
                  <p className="mt-1 break-all text-sm text-gray-600">{currentAudit.url}</p>
                  {currentAudit.summary?.score_explanation && (
                    <p className="mt-4 max-w-3xl text-sm leading-6 text-gray-600">{currentAudit.summary.score_explanation}</p>
                  )}
                </div>

                <div className="bg-gray-950 p-6 text-white">
                  <p className="text-sm text-blue-100">Overall score</p>
                  <p className={`mt-2 text-5xl font-black ${scoreColorClass(currentAudit.score)}`}>
                    {currentAudit.score}
                  </p>
                  <div className="mt-4 h-3 rounded-full bg-white/20">
                    <div className={`h-3 rounded-full ${progressColorClass(currentAudit.score)}`} style={{ width: `${currentAudit.score}%` }} />
                  </div>
                </div>
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Card className="p-5 text-center">
                <p className="text-3xl font-bold text-emerald-600">{currentAudit.issues.passed}</p>
                <p className="mt-1 text-sm text-gray-600">Passed</p>
              </Card>
              <Card className="p-5 text-center">
                <p className="text-3xl font-bold text-amber-600">{currentAudit.issues.warnings}</p>
                <p className="mt-1 text-sm text-gray-600">Warnings</p>
              </Card>
              <Card className="p-5 text-center">
                <p className="text-3xl font-bold text-red-600">{currentAudit.issues.errors}</p>
                <p className="mt-1 text-sm text-gray-600">Errors</p>
              </Card>
            </div>

            <Card className="p-6">
              <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
                <div>
                  <h3 className="text-xl font-bold text-gray-950">Top-5 проблем</h3>
                  <p className="mt-1 text-sm text-gray-600">Публичная выдача показывает краткий приоритетный срез, без приватной истории проекта.</p>
                </div>
                <Link to="/register">
                  <Button type="button" variant="outline">Разобрать все правки в кабинете</Button>
                </Link>
              </div>

              {topFindings.length > 0 ? (
                <div className="space-y-3">
                  {topFindings.map((detail, index) => (
                    <div key={`${detail.code}-${index}`} className={`rounded-xl border p-4 ${severityBadgeClass(detail)}`}>
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <h4 className="font-semibold">{index + 1}. {detail.title}</h4>
                        <span className="rounded-full bg-white/70 px-2.5 py-1 text-xs font-medium uppercase tracking-wide">
                          {detail.severity || detail.status}
                        </span>
                      </div>
                      <p className="text-sm leading-6">{detail.description}</p>
                      {detail.recommendation && (
                        <p className="mt-3 text-sm font-medium">Что сделать: {detail.recommendation}</p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="rounded-xl bg-emerald-50 p-4 text-sm text-emerald-700">Критичных публичных проблем не найдено.</p>
              )}
            </Card>

            <Card className="border-blue-100 bg-blue-50 p-6">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <h3 className="text-lg font-bold text-blue-950">Нужны история, приватные задачи и HITL-одобрение?</h3>
                  <p className="mt-1 text-sm text-blue-800">
                    Зарегистрируйтесь, чтобы привязать аудит к проекту, видеть историю и согласовывать изменения через DiffViewer.
                  </p>
                </div>
                <Link to="/register">
                  <Button type="button">Перейти в полный режим</Button>
                </Link>
              </div>
            </Card>
          </section>
        )}
      </main>
    </div>
  )
}

export default Audit
