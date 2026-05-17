import { useEffect } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadProjectDetails } from '../store/slices/dashboardSlice'
import ProjectAuditTab from '../components/projects/ProjectAuditTab'
import ProjectIntegrationsTab from '../components/projects/ProjectIntegrationsTab'
import Card from '../components/ui/Card'
import Loader from '../components/ui/Loader'
import { cn } from '../utils/classNames'
import { formatProjectStatus } from '../utils/format'

type ProjectDetailTab = 'overview' | 'audit' | 'integrations'

const projectTabs: Array<{ id: ProjectDetailTab; label: string; description: string }> = [
  {
    id: 'overview',
    label: 'Обзор',
    description: 'Детали проекта, быстрый доступ к SEO-инструментам и статус сайта.',
  },
  {
    id: 'audit',
    label: 'Расширенный аудит',
    description: 'Глубокая проверка проекта с увеличенной глубиной обхода и JS-rendering.',
  },
  {
    id: 'integrations',
    label: 'Интеграции',
    description: 'Подключение CMS, аналитики и инструментов вебмастера на уровне проекта.',
  },
]

const formatProjectDate = (value?: string | null) => {
  if (!value) {
    return 'Не доступно'
  }

  const parsedDate = new Date(value)
  return Number.isNaN(parsedDate.getTime()) ? value : parsedDate.toLocaleString('ru-RU')
}

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const dispatch = useAppDispatch()
  const { currentProject, loading, error } = useAppSelector((state) => state.dashboard)
  const rawTab = searchParams.get('tab')
  const activeTab: ProjectDetailTab = rawTab === 'integrations' || rawTab === 'audit' ? rawTab : 'overview'

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
          Назад к проектам
        </Link>
        <Card className="p-6">
          <h1 className="text-2xl font-semibold text-gray-900">Проект не найден</h1>
          <p className="mt-2 text-gray-600">{error || 'Проект недоступен или у вас нет к нему доступа.'}</p>
        </Card>
      </div>
    )
  }

  const tools = [
    {
      name: 'Ключевые слова',
      description: 'Исследуйте ключевые фразы и ведите список отслеживаемых запросов для проекта.',
      link: `/dashboard/keywords?project=${currentProject.id}`,
    },
    {
      name: 'Оптимизация контента',
      description: 'Оцените текст страницы и получите практические рекомендации по улучшению.',
      link: `/dashboard/content?project=${currentProject.id}`,
    },
    {
      name: 'Ссылки',
      description: 'Просмотрите исходящие ссылки страниц проекта и оцените их качество.',
      link: `/dashboard/backlinks?project=${currentProject.id}`,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <Link to="/dashboard/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
          Назад к проектам
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

      <Card className="overflow-hidden p-0">
        <div className="grid gap-2 p-2 md:grid-cols-3">
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
            <h2 className="text-xl font-semibold text-gray-900">Детали проекта</h2>
            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Статус</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectStatus(currentProject.status)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Создан</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(currentProject.createdAt)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Обновлён</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(currentProject.updatedAt)}</p>
              </div>
            </div>
          </Card>

          <div>
            <h2 className="mb-4 text-xl font-semibold text-gray-900">SEO-инструменты</h2>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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

      {activeTab === 'audit' && (
        <ProjectAuditTab projectId={currentProject.id} projectUrl={currentProject.url} />
      )}
    </div>
  )
}

export default ProjectDetail
