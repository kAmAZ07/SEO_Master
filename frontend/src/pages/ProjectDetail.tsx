import { useEffect } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadProjectDetails } from '../store/slices/dashboardSlice'
import ProjectIntegrationsTab from '../components/projects/ProjectIntegrationsTab'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'
import { cn } from '../utils/classNames'

type ProjectDetailTab = 'overview' | 'integrations'

const projectTabs: Array<{ id: ProjectDetailTab; label: string; description: string }> = [
  {
    id: 'overview',
    label: 'РћР±Р·РѕСЂ',
    description: 'Р”РµС‚Р°Р»Рё РїСЂРѕРµРєС‚Р°, Р±С‹СЃС‚СЂС‹Р№ РґРѕСЃС‚СѓРї Рє SEO-РёРЅСЃС‚СЂСѓРјРµРЅС‚Р°Рј Рё СЃС‚Р°С‚СѓСЃ СЃР°Р№С‚Р°.',
  },
  {
    id: 'integrations',
    label: 'РРЅС‚РµРіСЂР°С†РёРё',
    description: 'Per-project connections for Tilda, WordPress, GSC, GA4, and Yandex without shared global credentials.',
  },
]

const formatProjectDate = (value?: string | null) => {
  if (!value) {
    return 'Unavailable'
  }

  const parsedDate = new Date(value)
  return Number.isNaN(parsedDate.getTime()) ? value : parsedDate.toLocaleString()
}

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const dispatch = useAppDispatch()
  const { currentProject, loading, error } = useAppSelector((state) => state.dashboard)
  const activeTab: ProjectDetailTab = searchParams.get('tab') === 'integrations' ? 'integrations' : 'overview'

  useEffect(() => {
    if (id) {
      void dispatch(loadProjectDetails(id))
    }
  }, [dispatch, id])

  const handleSelectTab = (tab: ProjectDetailTab) => {
    const nextParams = new URLSearchParams(searchParams)

    if (tab === 'overview') {
      nextParams.delete('tab')
    } else {
      nextParams.set('tab', tab)
    }

    setSearchParams(nextParams)
  }

  if (loading && !currentProject) {
    return <Loader />
  }

  if (!currentProject) {
    return (
      <div className="space-y-4">
        <Link to="/dashboard/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
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
      link: `/dashboard/keywords?project=${currentProject.id}`,
    },
    {
      name: 'Content optimization',
      description: 'Analyze page copy and get practical recommendations.',
      link: `/dashboard/content?project=${currentProject.id}`,
    },
    {
      name: 'Backlink review',
      description: 'Inspect discovered referring pages and basic backlink quality signals.',
      link: `/dashboard/backlinks?project=${currentProject.id}`,
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <Link to="/dashboard/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
            Back to projects
          </Link>
          <h1 className="mt-2 text-3xl font-bold text-gray-900">{currentProject.name}</h1>
          <a
            href={currentProject.url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-block text-blue-600 hover:text-blue-700"
          >
            {currentProject.url}
          </a>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button type="button" variant="outline" onClick={() => handleSelectTab('integrations')}>
            РРЅС‚РµРіСЂР°С†РёРё
          </Button>
          <Link to={`/audit?project=${currentProject.id}`}>
            <Button className="w-full sm:w-auto">Run audit</Button>
          </Link>
        </div>
      </div>

      <Card className="overflow-hidden p-0">
        <div className="grid gap-2 p-2 md:grid-cols-2">
          {projectTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => handleSelectTab(tab.id)}
              className={cn(
                'rounded-xl px-4 py-3 text-left transition-colors',
                activeTab === tab.id ? 'bg-blue-50 text-blue-900' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900',
              )}
            >
              <span className="text-sm font-semibold">{tab.label}</span>
              <span className="mt-1 block text-sm leading-5 opacity-80">{tab.description}</span>
            </button>
          ))}
        </div>
      </Card>

      {activeTab === 'overview' && (
        <div className="space-y-6">
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
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(currentProject.createdAt)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Updated</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(currentProject.updatedAt)}</p>
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
      )}

      {activeTab === 'integrations' && (
        <ProjectIntegrationsTab projectId={currentProject.id} projectUrl={currentProject.url} />
      )}
    </div>
  )
}

export default ProjectDetail

