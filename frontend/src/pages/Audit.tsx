import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { startAudit, fetchAuditHistory, pollAuditStatus, setCurrentAudit } from '../store/slices/auditSlice'
import type { AuditFinding, AuditPage } from '../types/audit'
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

const formatPageMeta = (page: AuditPage) => [page.title, page.description, page.h1].filter(Boolean).join(' | ')

const Audit = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')

  const dispatch = useAppDispatch()
  const { currentAudit, history, loading, polling, error } = useAppSelector((state) => state.audit)
  const [url, setUrl] = useState('')

  useEffect(() => {
    dispatch(fetchAuditHistory(projectId ? Number(projectId) : undefined))
  }, [dispatch, projectId])

  useEffect(() => {
    if (!polling || !currentAudit?.uid) {
      return undefined
    }

    const timer = window.setInterval(() => {
      dispatch(pollAuditStatus(currentAudit.uid))
    }, 2000)

    dispatch(pollAuditStatus(currentAudit.uid))

    return () => {
      window.clearInterval(timer)
    }
  }, [dispatch, polling, currentAudit?.uid])

  const handleStartAudit = async (event: React.FormEvent) => {
    event.preventDefault()
    await dispatch(startAudit({ url, projectId: projectId ? Number(projectId) : undefined }))
    setUrl('')
  }

  const groupedFindings = useMemo(() => {
    const groups: Record<string, AuditFinding[]> = {}

    for (const detail of currentAudit?.details ?? []) {
      const key = detail.category || 'general'
      groups[key] = [...(groups[key] ?? []), detail]
    }

    return Object.entries(groups)
  }, [currentAudit?.details])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Site audit</h1>
        <p className="mt-1 text-gray-600">Run a technical SEO audit and inspect every issue with context.</p>
      </div>

      <Card className="p-6">
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Start a new audit</h2>
        <form onSubmit={handleStartAudit} className="flex flex-col gap-3 md:flex-row">
          <Input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Starting...' : 'Run audit'}
          </Button>
        </form>
      </Card>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {currentAudit && polling && currentAudit.status !== 'completed' && currentAudit.status !== 'failed' && (
        <Card className="p-6">
          <div className="py-6 text-center">
            <Loader />
            <p className="mt-4 text-gray-700">Audit in progress...</p>
            <p className="mt-2 text-sm text-gray-500">We are crawling pages, validating metadata, links, and CWV signals.</p>
          </div>
        </Card>
      )}

      {currentAudit && currentAudit.status === 'completed' && (
        <Card className="p-6">
          <div className="mb-6 flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-900">Audit results</h2>
              <p className="mt-1 text-sm text-gray-600">{currentAudit.url}</p>
              {currentAudit.summary?.score_explanation && (
                <p className="mt-3 max-w-3xl text-sm text-gray-600">{currentAudit.summary.score_explanation}</p>
              )}
            </div>

            <div className="min-w-[220px] rounded-xl bg-gray-50 p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700">Overall score</span>
                <span className={`text-3xl font-bold ${scoreColorClass(currentAudit.score)}`}>
                  {currentAudit.score}/100
                </span>
              </div>
              <div className="h-3 rounded-full bg-gray-200">
                <div className={`h-3 rounded-full transition-all ${progressColorClass(currentAudit.score)}`} style={{ width: `${currentAudit.score}%` }} />
              </div>
            </div>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-emerald-600">{currentAudit.issues.passed}</p>
              <p className="mt-1 text-sm text-gray-600">Passed</p>
            </div>
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-amber-600">{currentAudit.issues.warnings}</p>
              <p className="mt-1 text-sm text-gray-600">Warnings</p>
            </div>
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-red-600">{currentAudit.issues.errors}</p>
              <p className="mt-1 text-sm text-gray-600">Errors</p>
            </div>
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-gray-900">{currentAudit.summary?.coverage?.processed ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Pages processed</p>
            </div>
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-gray-900">{currentAudit.summary?.coverage?.attempted ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Pages attempted</p>
            </div>
            <div className="rounded-xl bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-gray-900">{currentAudit.summary?.links_checked ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Links checked</p>
            </div>
          </div>

          {currentAudit.summary?.score_breakdown && (
            <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50 p-4">
              <h3 className="mb-3 font-semibold text-gray-900">Score breakdown</h3>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                <div>
                  <p className="text-sm text-gray-500">Base score</p>
                  <p className="text-lg font-semibold text-gray-900">{currentAudit.summary.score_breakdown.base_score}</p>
                </div>
                <div>
                  <p className="text-sm text-gray-500">Penalty points</p>
                  <p className="text-lg font-semibold text-red-600">-{currentAudit.summary.score_breakdown.penalty_points}</p>
                </div>
                <div>
                  <p className="text-sm text-gray-500">Coverage bonus</p>
                  <p className="text-lg font-semibold text-emerald-600">+{currentAudit.summary.score_breakdown.coverage_bonus}</p>
                </div>
                <div>
                  <p className="text-sm text-gray-500">Crawl bonus</p>
                  <p className="text-lg font-semibold text-emerald-600">+{currentAudit.summary.score_breakdown.crawl_bonus}</p>
                </div>
              </div>
            </div>
          )}

          {currentAudit.summary?.cwv && (
            <div className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
              <h3 className="mb-3 font-semibold text-gray-900">Core Web Vitals snapshot</h3>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <div className="rounded-lg bg-gray-50 p-4">
                  <p className="text-sm text-gray-500">LCP</p>
                  <p className="text-lg font-semibold text-gray-900">
                    {String(currentAudit.summary.cwv.LCP_ms ?? 'n/a')} ms
                  </p>
                  <p className="mt-1 text-sm text-gray-600">Grade: {String(currentAudit.summary.cwv.LCP_grade ?? 'unknown')}</p>
                </div>
                <div className="rounded-lg bg-gray-50 p-4">
                  <p className="text-sm text-gray-500">FID</p>
                  <p className="text-lg font-semibold text-gray-900">
                    {String(currentAudit.summary.cwv.FID_ms ?? 'n/a')} ms
                  </p>
                  <p className="mt-1 text-sm text-gray-600">Grade: {String(currentAudit.summary.cwv.FID_grade ?? 'unknown')}</p>
                </div>
                <div className="rounded-lg bg-gray-50 p-4">
                  <p className="text-sm text-gray-500">CLS</p>
                  <p className="text-lg font-semibold text-gray-900">{String(currentAudit.summary.cwv.CLS ?? 'n/a')}</p>
                  <p className="mt-1 text-sm text-gray-600">Grade: {String(currentAudit.summary.cwv.CLS_grade ?? 'unknown')}</p>
                </div>
              </div>
            </div>
          )}

          <div className="space-y-6">
            <div>
              <h3 className="mb-4 font-semibold text-gray-900">Detailed findings</h3>
              <div className="space-y-6">
                {groupedFindings.map(([groupName, findings]) => (
                  <div key={groupName}>
                    <h4 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">{groupName.replace(/_/g, ' ')}</h4>
                    <div className="space-y-3">
                      {findings.map((detail, index) => (
                        <div key={`${detail.code}-${index}`} className={`rounded-xl border p-4 ${severityBadgeClass(detail)}`}>
                          <div className="mb-2 flex items-center justify-between gap-3">
                            <h5 className="font-semibold">{detail.title}</h5>
                            <span className="rounded-full bg-white/70 px-2.5 py-1 text-xs font-medium uppercase tracking-wide">
                              {detail.severity || detail.status}
                            </span>
                          </div>
                          <p className="text-sm leading-6">{detail.description}</p>
                          {detail.recommendation && (
                            <p className="mt-3 text-sm font-medium">Recommendation: {detail.recommendation}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {currentAudit.pages && currentAudit.pages.length > 0 && (
              <div>
                <h3 className="mb-4 font-semibold text-gray-900">Crawled pages</h3>
                <div className="space-y-3">
                  {currentAudit.pages.map((page, index) => (
                    <div key={`${page.url}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                        <div>
                          <p className="font-medium text-gray-900">{page.url}</p>
                          {formatPageMeta(page) && <p className="mt-1 text-sm text-gray-600">{formatPageMeta(page)}</p>}
                          {page.error && <p className="mt-2 text-sm text-red-600">Fetch issue: {page.error}</p>}
                        </div>
                        <div className="text-sm text-gray-500">
                          {typeof page.status_code === 'number' && Number.isFinite(page.status_code) ? `HTTP ${page.status_code}` : 'No HTTP status'}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {history.length > 0 && (
        <Card className="p-6">
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Audit history</h2>
          <div className="space-y-3">
            {history.map((audit) => (
              <div key={audit.uid} className="flex flex-col gap-3 rounded-xl bg-gray-50 p-4 transition-colors hover:bg-gray-100 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-gray-900">{audit.url}</p>
                  <p className="text-sm text-gray-600">{new Date(audit.createdAt).toLocaleString('ru-RU')}</p>
                </div>
                <div className="flex items-center gap-4">
                  <span className={`text-2xl font-bold ${scoreColorClass(audit.score)}`}>{audit.score}</span>
                  <Button type="button" onClick={() => dispatch(setCurrentAudit(audit))}>
                    View
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

export default Audit
