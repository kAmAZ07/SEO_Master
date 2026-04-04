import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadStats } from '../store/slices/dashboardSlice'
import Card from '../components/ui/Card'
import Loader from '../components/ui/Loader'

const Dashboard = () => {
  const dispatch = useAppDispatch()
  const { stats, loading, error } = useAppSelector((state) => state.dashboard)

  useEffect(() => {
    void dispatch(loadStats())
  }, [dispatch])

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
