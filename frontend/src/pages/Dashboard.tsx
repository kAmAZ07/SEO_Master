import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactDiffViewer from 'react-diff-viewer-continued'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadStats } from '../store/slices/dashboardSlice'
import { approveHITLTask, loadHITLTasks, rejectHITLTask } from '../store/slices/hitlSlice'
import type { HITLTask } from '../types/hitl'
import Card from '../components/ui/Card'
import Loader from '../components/ui/Loader'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'

const stringifyDiffValue = (value: Record<string, unknown>) => JSON.stringify(value, null, 2)

const buildTaskTitle = (task: HITLTask) => {
  const afterUrl = typeof task.diffData.after.url === 'string' ? task.diffData.after.url : ''
  const beforeUrl = typeof task.diffData.before.url === 'string' ? task.diffData.before.url : ''
  return afterUrl || beforeUrl || `Task ${task.taskId}`
}

const Dashboard = () => {
  const dispatch = useAppDispatch()
  const { stats, loading, error } = useAppSelector((state) => state.dashboard)
  const hitlState = useAppSelector((state) => state.hitl)
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null)
  const [comments, setComments] = useState<Record<string, string>>({})
  const [submittingTaskId, setSubmittingTaskId] = useState<string | null>(null)

  useEffect(() => {
    void dispatch(loadStats())
    void dispatch(loadHITLTasks())
  }, [dispatch])

  const pendingTasks = useMemo(() => hitlState.tasks.filter((task) => task.status === 'pending'), [hitlState.tasks])

  const handleApprove = async (taskId: string) => {
    setSubmittingTaskId(taskId)
    try {
      await dispatch(approveHITLTask({ taskId, approved: true, comment: comments[taskId] || undefined }))
    } finally {
      setSubmittingTaskId(null)
    }
  }

  const handleReject = async (taskId: string) => {
    setSubmittingTaskId(taskId)
    try {
      await dispatch(rejectHITLTask({ taskId, approved: false, comment: comments[taskId] || undefined }))
    } finally {
      setSubmittingTaskId(null)
    }
  }

  if (loading && !stats) {
    return <Loader />
  }

  const statCards = [
    { title: 'Projects', value: stats?.totalProjects ?? 0, link: '/projects' },
    { title: 'Active audits', value: stats?.activeAudits ?? 0, link: '/audit' },
    { title: 'Tracked keywords', value: stats?.totalKeywords ?? 0, link: '/keywords' },
    { title: 'Backlinks discovered', value: stats?.totalBacklinks ?? 0, link: '/backlinks' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="mt-1 text-gray-600">A quick overview of your current SEO workspace.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {hitlState.error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{hitlState.error}</div>}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-4">
        {statCards.map((card) => (
          <Link key={card.title} to={card.link}>
            <Card className="h-full cursor-pointer p-6 transition-shadow hover:shadow-lg">
              <p className="text-sm font-medium text-gray-500">{card.title}</p>
              <p className="mt-3 text-3xl font-bold text-gray-900">{card.value}</p>
            </Card>
          </Link>
        ))}
      </div>

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">HITL approvals</h2>
            <p className="mt-1 text-sm text-gray-600">Review proposed changes, inspect the diff, and approve or reject them from the private workspace.</p>
          </div>
          <span className="rounded-full bg-blue-100 px-3 py-1 text-sm font-semibold text-blue-700">
            {pendingTasks.length} pending
          </span>
        </div>

        {hitlState.loading && !pendingTasks.length ? (
          <Loader />
        ) : pendingTasks.length ? (
          <div className="space-y-4">
            {pendingTasks.map((task) => {
              const isExpanded = expandedTaskId === task.id
              const isSubmitting = submittingTaskId === task.taskId
              const beforeValue = stringifyDiffValue(task.diffData.before)
              const afterValue = stringifyDiffValue(task.diffData.after)

              return (
                <div key={task.id} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-lg font-semibold text-gray-900">{buildTaskTitle(task)}</p>
                        <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-amber-700">
                          Pending approval
                        </span>
                      </div>
                      <p className="mt-2 text-sm text-gray-600">
                        Project: {task.projectId} • Created: {task.createdAt ? new Date(task.createdAt).toLocaleString('ru-RU') : 'n/a'}
                      </p>
                      {task.recommendation && (
                        <p className="mt-3 text-sm text-gray-700">Recommendation: {task.recommendation}</p>
                      )}
                      {task.impactScore != null && (
                        <p className="mt-2 text-sm text-gray-600">Impact score: {task.impactScore.toFixed(2)}</p>
                      )}
                    </div>

                    <div className="flex flex-wrap gap-2">
                      <Button type="button" variant="outline" onClick={() => setExpandedTaskId(isExpanded ? null : task.id)}>
                        {isExpanded ? 'Hide diff' : 'Show diff'}
                      </Button>
                      <Button type="button" disabled={isSubmitting} onClick={() => void handleApprove(task.taskId)}>
                        {isSubmitting ? 'Saving...' : 'Approve'}
                      </Button>
                      <Button type="button" variant="destructive" disabled={isSubmitting} onClick={() => void handleReject(task.taskId)}>
                        Reject
                      </Button>
                    </div>
                  </div>

                  <div className="mt-4">
                    <Input
                      value={comments[task.taskId] ?? ''}
                      onChange={(event) => setComments((current) => ({ ...current, [task.taskId]: event.target.value }))}
                      placeholder="Optional reviewer note"
                    />
                  </div>

                  {isExpanded && (
                    <div className="mt-4 overflow-hidden rounded-xl border border-gray-200 bg-white">
                      <div className="border-b border-gray-200 px-4 py-3 text-sm text-gray-600">
                        Diff viewer compares the previous snapshot with the proposed change.
                      </div>
                      <div className="overflow-x-auto">
                        <ReactDiffViewer
                          oldValue={beforeValue}
                          newValue={afterValue}
                          splitView
                          hideLineNumbers={false}
                          useDarkTheme={false}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No pending approvals right now.</p>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-gray-900">Recent projects</h2>
            <Link to="/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
              View all
            </Link>
          </div>

          {stats?.recentProjects?.length ? (
            <div className="space-y-3">
              {stats.recentProjects.map((project) => (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className="block rounded-lg border border-gray-200 px-4 py-3 transition-colors hover:border-blue-200 hover:bg-blue-50/40"
                >
                  <p className="font-medium text-gray-900">{project.name}</p>
                  <p className="mt-1 text-sm text-gray-600">{project.url}</p>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No projects yet. Create one to start tracking audits and changes.</p>
          )}
        </Card>

        <Card className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-gray-900">Recent audits</h2>
            <Link to="/audit" className="text-sm font-medium text-blue-600 hover:text-blue-700">
              Open audit tool
            </Link>
          </div>

          {stats?.recentAudits?.length ? (
            <div className="space-y-3">
              {stats.recentAudits.map((audit) => (
                <div key={audit.id} className="rounded-lg border border-gray-200 px-4 py-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-medium text-gray-900">{audit.url}</p>
                      <p className="mt-1 text-sm text-gray-600">Status: {audit.status}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs uppercase tracking-wide text-gray-400">Score</p>
                      <p className="text-2xl font-bold text-gray-900">{audit.score}/100</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">No audits have been saved yet.</p>
          )}
        </Card>
      </div>
    </div>
  )
}

export default Dashboard
