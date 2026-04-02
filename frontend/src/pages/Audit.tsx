import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { fetchAuditHistory, pollAuditStatus, setCurrentAudit, startAudit } from '../store/slices/auditSlice'
import type { AuditStatus } from '../types/audit'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'

const Audit = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')

  const dispatch = useAppDispatch()
  const { isAuthenticated } = useAppSelector((state) => state.auth)
  const { currentAudit, history, loading } = useAppSelector((state) => state.audit)
  const [url, setUrl] = useState('')

  useEffect(() => {
    if (isAuthenticated) {
      dispatch(fetchAuditHistory(projectId || undefined))
    }
  }, [dispatch, isAuthenticated, projectId])

  const handleStartAudit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!url.trim()) {
      return
    }

    await dispatch(startAudit({ url: url.trim(), projectId: projectId || undefined }))
    setUrl('')
  }

  const isInProgress =
    !!currentAudit &&
    (currentAudit.status === 'queued' ||
      currentAudit.status === 'running' ||
      currentAudit.status === 'pending' ||
      currentAudit.status === 'in_progress' ||
      currentAudit.status === 'processing')

  const auditUid = currentAudit?.uid || currentAudit?.id

  useEffect(() => {
    if (!auditUid || !isInProgress) {
      return
    }

    const timer = window.setInterval(() => {
      dispatch(pollAuditStatus(auditUid))
    }, 3000)

    return () => {
      window.clearInterval(timer)
    }
  }, [auditUid, dispatch, isInProgress])

  const completedAudit = useMemo(() => {
    if (!currentAudit || currentAudit.status !== 'completed') {
      return null
    }

    return currentAudit
  }, [currentAudit])

  const auditScore = completedAudit?.score ?? 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Site Audit</h1>
        <p className="mt-1 text-gray-600">Run and inspect SEO audit reports for your project pages.</p>
      </div>

      <Card>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Start new audit</h2>
        <form onSubmit={handleStartAudit} className="flex gap-3">
          <Input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Starting...' : 'Start audit'}
          </Button>
        </form>
      </Card>

      {isInProgress && (
        <Card>
          <div className="space-y-2 py-2">
            <p className="text-gray-700">Audit in progress...</p>
            <p className="text-sm text-gray-500">This can take a few minutes.</p>
            {typeof currentAudit?.progress === 'number' && (
              <div className="h-2 w-full rounded-full bg-gray-200">
                <div
                  className="h-2 rounded-full bg-blue-600 transition-all"
                  style={{ width: `${Math.max(0, Math.min(currentAudit.progress, 100))}%` }}
                />
              </div>
            )}
          </div>
        </Card>
      )}

      {completedAudit && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Audit results</h2>

          <div className="mb-6">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">Overall score</span>
              <span
                className={`text-2xl font-bold ${
                  auditScore >= 80 ? 'text-green-600' : auditScore >= 50 ? 'text-yellow-600' : 'text-red-600'
                }`}
              >
                {auditScore}/100
              </span>
            </div>
            <div className="h-3 w-full rounded-full bg-gray-200">
              <div
                className={`h-3 rounded-full transition-all ${
                  auditScore >= 80 ? 'bg-green-600' : auditScore >= 50 ? 'bg-yellow-600' : 'bg-red-600'
                }`}
                style={{ width: `${auditScore}%` }}
              />
            </div>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-green-600">{completedAudit.issues?.passed ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Passed</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-yellow-600">{completedAudit.issues?.warnings ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Warnings</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-red-600">{completedAudit.issues?.errors ?? 0}</p>
              <p className="mt-1 text-sm text-gray-600">Errors</p>
            </div>
          </div>

          {completedAudit.details && completedAudit.details.length > 0 && (
            <div className="space-y-4">
              <h3 className="font-semibold text-gray-900">Detailed results</h3>
              {completedAudit.details.map((detail, index) => (
                <div
                  key={index}
                  className="border-l-4 py-2 pl-4"
                  style={{
                    borderColor:
                      detail.status === 'error'
                        ? '#ef4444'
                        : detail.status === 'warning'
                          ? '#f59e0b'
                          : '#10b981',
                  }}
                >
                  <h4 className="font-medium text-gray-900">{detail.title}</h4>
                  <p className="mt-1 text-sm text-gray-600">{detail.description}</p>
                  {detail.recommendation && <p className="mt-2 text-sm text-blue-600">Tip: {detail.recommendation}</p>}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {!isAuthenticated && (
        <Card>
          <p className="text-sm text-gray-600">
            Audit history, project linking, and task orchestration are available in your account dashboard.
          </p>
          <div className="mt-3 flex gap-3">
            <Link to="/login">
              <Button>Sign in</Button>
            </Link>
            <Link to="/register">
              <Button className="bg-slate-700 hover:bg-slate-800">Create account</Button>
            </Link>
          </div>
        </Card>
      )}

      {isAuthenticated && history.length > 0 && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Audit history</h2>
          <div className="space-y-3">
            {history.map((audit: AuditStatus) => {
              const score = audit.score ?? 0
              const date = audit.createdAt ? new Date(audit.createdAt).toLocaleString('ru-RU') : '-'

              return (
                <div
                  key={audit.id ?? audit.uid ?? `${audit.url}-${date}`}
                  className="flex items-center justify-between rounded-lg bg-gray-50 p-4 transition-colors hover:bg-gray-100"
                >
                  <div className="flex-1">
                    <p className="font-medium text-gray-900">{audit.url}</p>
                    <p className="text-sm text-gray-600">{date}</p>
                  </div>
                  <div className="flex items-center gap-4">
                    <span
                      className={`text-2xl font-bold ${
                        score >= 80 ? 'text-green-600' : score >= 50 ? 'text-yellow-600' : 'text-red-600'
                      }`}
                    >
                      {score}
                    </span>
                    <Button onClick={() => dispatch(setCurrentAudit(audit))}>View</Button>
                  </div>
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </div>
  )
}

export default Audit
