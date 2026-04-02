import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { FolderOpenDot, KeyRound, Link2, Radar } from 'lucide-react'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { fetchDashboardStats } from '../store/slices/dashboardSlice'
import { Project, RecentAudit } from '../types/dashboard'
import Card from '../components/ui/Card'
import Loader from '../components/ui/Loader'

const Dashboard = () => {
  const dispatch = useAppDispatch()
  const { stats, loading } = useAppSelector((state) => state.dashboard)

  useEffect(() => {
    dispatch(fetchDashboardStats())
  }, [dispatch])

  if (loading) {
    return <Loader />
  }

  const statsCards = [
    {
      title: 'Total projects',
      value: stats?.totalProjects || 0,
      Icon: FolderOpenDot,
      link: '/app/projects',
    },
    {
      title: 'Active audits',
      value: stats?.activeAudits || 0,
      Icon: Radar,
      link: '/app/audit',
    },
    {
      title: 'Tracked keywords',
      value: stats?.totalKeywords || 0,
      Icon: KeyRound,
      link: '/app/keywords',
    },
    {
      title: 'Backlinks',
      value: stats?.totalBacklinks || 0,
      Icon: Link2,
      link: '/app/backlinks',
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="mt-1 text-gray-600">Overview of your SEO projects and recent activity.</p>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        {statsCards.map((stat) => (
          <Link key={stat.title} to={stat.link}>
            <Card className="cursor-pointer transition-shadow hover:shadow-lg">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-600">{stat.title}</p>
                  <p className="mt-2 text-3xl font-bold text-gray-900">{stat.value}</p>
                </div>
                <stat.Icon className="h-9 w-9 text-gray-400" />
              </div>
            </Card>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Recent projects</h2>
          {stats?.recentProjects && stats.recentProjects.length > 0 ? (
            <div className="space-y-3">
              {stats.recentProjects.map((project: Project) => (
                <div
                  key={project.id}
                  className="flex items-center justify-between rounded-lg bg-gray-50 p-3 transition-colors hover:bg-gray-100"
                >
                  <div>
                    <h3 className="font-medium text-gray-900">{project.name}</h3>
                    <p className="text-sm text-gray-600">{project.url}</p>
                  </div>
                  <Link to={`/app/projects/${project.id}`} className="text-sm font-medium text-blue-600 hover:text-blue-700">
                    Open
                  </Link>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-8 text-center text-gray-500">No projects yet.</p>
          )}
        </Card>

        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Recent audits</h2>
          {stats?.recentAudits && stats.recentAudits.length > 0 ? (
            <div className="space-y-3">
              {stats.recentAudits.map((audit: RecentAudit) => (
                <div key={audit.id} className="flex items-center justify-between rounded-lg bg-gray-50 p-3">
                  <div>
                    <h3 className="font-medium text-gray-900">{audit.url}</h3>
                    <p className="text-sm text-gray-600">Score: {audit.score}/100</p>
                  </div>
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-medium ${
                      audit.status === 'completed'
                        ? 'bg-green-100 text-green-800'
                        : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {audit.status === 'completed' ? 'Completed' : 'In progress'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="py-8 text-center text-gray-500">No audits yet.</p>
          )}
        </Card>
      </div>
    </div>
  )
}

export default Dashboard
