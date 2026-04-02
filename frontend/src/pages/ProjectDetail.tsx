import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { FileSearch, KeyRound, Link2, TextSearch } from 'lucide-react'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { fetchProjectDetails } from '../store/slices/dashboardSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const dispatch = useAppDispatch()
  const { currentProject, loading } = useAppSelector((state) => state.dashboard)

  useEffect(() => {
    if (id) {
      dispatch(fetchProjectDetails(id))
    }
  }, [dispatch, id])

  if (loading || !currentProject) {
    return <Loader />
  }

  const tools = [
    {
      name: 'Site audit',
      description: 'Run a full technical and SEO audit.',
      link: `/app/audit?project=${id}`,
      Icon: FileSearch,
    },
    {
      name: 'Keywords',
      description: 'Research and track target search terms.',
      link: `/app/keywords?project=${id}`,
      Icon: KeyRound,
    },
    {
      name: 'Content optimization',
      description: 'Analyze and improve page content quality.',
      link: `/app/content?project=${id}`,
      Icon: TextSearch,
    },
    {
      name: 'Backlinks',
      description: 'Monitor and evaluate inbound links.',
      link: `/app/backlinks?project=${id}`,
      Icon: Link2,
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/app/projects" className="mb-2 inline-block text-sm text-blue-600 hover:text-blue-700">
            Back to projects
          </Link>
          <h1 className="text-3xl font-bold text-gray-900">{currentProject.name}</h1>
          <a
            href={currentProject.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-block text-blue-600 hover:text-blue-700"
          >
            {currentProject.url} (open)
          </a>
        </div>
        <Button>Project settings</Button>
      </div>

      {currentProject.description && (
        <Card>
          <p className="text-gray-700">{currentProject.description}</p>
        </Card>
      )}

      <div>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">SEO tools</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {tools.map((tool) => (
            <Link key={tool.name} to={tool.link}>
              <Card className="h-full cursor-pointer transition-shadow hover:shadow-lg">
                <div className="flex items-start gap-4">
                  <tool.Icon className="mt-1 h-5 w-5 text-gray-500" />
                  <div className="flex-1">
                    <h3 className="font-semibold text-gray-900">{tool.name}</h3>
                    <p className="mt-1 text-sm text-gray-600">{tool.description}</p>
                  </div>
                  <span className="text-blue-600">Open</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      </div>

      <Card>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Project stats</h2>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div className="text-center">
            <p className="text-3xl font-bold text-blue-600">{currentProject.stats?.audits || 0}</p>
            <p className="mt-1 text-sm text-gray-600">Audits</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-green-600">{currentProject.stats?.keywords || 0}</p>
            <p className="mt-1 text-sm text-gray-600">Keywords</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-purple-600">{currentProject.stats?.pages || 0}</p>
            <p className="mt-1 text-sm text-gray-600">Pages</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-orange-600">{currentProject.stats?.backlinks || 0}</p>
            <p className="mt-1 text-sm text-gray-600">Backlinks</p>
          </div>
        </div>
      </Card>
    </div>
  )
}

export default ProjectDetail
