import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadProjectDetails } from '../store/slices/dashboardSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const dispatch = useAppDispatch()
  const { currentProject, loading, error } = useAppSelector((state) => state.dashboard)

  useEffect(() => {
    if (id) {
      void dispatch(loadProjectDetails(id))
    }
  }, [dispatch, id])

  if (loading && !currentProject) {
    return <Loader />
  }

  if (!currentProject) {
    return (
      <div className="space-y-4">
        <Link to="/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
          Back to projects
        </Link>
        <Card className="p-6">
          <h1 className="text-2xl font-semibold text-gray-900">Project not found</h1>
          <p className="mt-2 text-gray-600">{error || 'This project is unavailable or you no longer have access to it.'}</p>
        </Card>
      </div>
    )
  }

  const tools = [
    {
      name: 'Technical audit',
      description: 'Run a crawl and review detailed issues, CWV checks, and scoring.',
      link: `/audit?project=${currentProject.id}`,
    },
    {
      name: 'Keyword research',
      description: 'Generate ideas and maintain a tracked list for this project.',
      link: `/keywords?project=${currentProject.id}`,
    },
    {
      name: 'Content optimization',
      description: 'Analyze page copy and get practical recommendations.',
      link: `/content?project=${currentProject.id}`,
    },
    {
      name: 'Backlink review',
      description: 'Inspect discovered referring pages and basic backlink quality signals.',
      link: `/backlinks?project=${currentProject.id}`,
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <Link to="/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
            Back to projects
          </Link>
          <h1 className="mt-2 text-3xl font-bold text-gray-900">{currentProject.name}</h1>
          <a href={currentProject.url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-blue-600 hover:text-blue-700">
            {currentProject.url}
          </a>
        </div>
        <Link to={`/audit?project=${currentProject.id}`}>
          <Button>Run audit</Button>
        </Link>
      </div>

      {currentProject.description && <Card className="p-6 text-gray-700">{currentProject.description}</Card>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Project details</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Status</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">{currentProject.status}</p>
          </div>
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Created</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">
              {currentProject.createdAt ? new Date(currentProject.createdAt).toLocaleString() : 'Unavailable'}
            </p>
          </div>
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Updated</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">
              {currentProject.updatedAt ? new Date(currentProject.updatedAt).toLocaleString() : 'Unavailable'}
            </p>
          </div>
        </div>
      </Card>

      <div>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">SEO tools</h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {tools.map((tool) => (
            <Link key={tool.name} to={tool.link}>
              <Card className="h-full p-6 transition-shadow hover:shadow-lg">
                <h3 className="text-lg font-semibold text-gray-900">{tool.name}</h3>
                <p className="mt-2 text-sm text-gray-600">{tool.description}</p>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

export default ProjectDetail
