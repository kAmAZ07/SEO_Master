import { useEffect } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { clearCurrentProject, loadProjectDetails } from '../store/slices/dashboardSlice'
import ProjectAuditTab from '../components/projects/ProjectAuditTab'
import ProjectFFScore from '../components/projects/ProjectFFScore'
import ProjectIntegrationsTab from '../components/projects/ProjectIntegrationsTab'
import ProjectOwnership from '../components/projects/ProjectOwnership'
import Button from '../components/ui/Button'
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

const CLAIM_REJECTED_MESSAGES: Record<string, string> = {
  invalid_claim: 'Проект создан, но публичный аудит к нему не присоединён: подтверждение авторства аудита не прошло.',
  already_claimed: 'Проект создан, но этот публичный аудит уже присоединён к другому проекту.',
  out_of_scope: 'Проект создан, но адрес аудита находится вне области проекта, поэтому аудит не присоединён.',
}

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const dispatch = useAppDispatch()
  const { currentProject, detailsLoading, detailsError, detailsNotFound } = useAppSelector((state) => state.dashboard)
  // Данные показываются только для проекта из маршрута: ответ по прежнему проекту не подменяет новый.
  const project = currentProject && currentProject.id === id ? currentProject : null
  const claimRejected = (location.state as { claimRejected?: string } | null)?.claimRejected
  const rawTab = searchParams.get('tab')
  const activeTab: ProjectDetailTab = rawTab === 'integrations' || rawTab === 'audit' ? rawTab : 'overview'

  useEffect(() => {
    if (!id) {
      return undefined
    }
    void dispatch(loadProjectDetails(id))
    return () => {
      dispatch(clearCurrentProject())
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

  if (!project && (detailsLoading || !detailsError)) {
    return <Loader />
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <Link to="/dashboard/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
          Назад к проектам
        </Link>
        <Card className="p-6">
          <div role="alert">
            <h1 className="text-2xl font-semibold text-gray-900">
              {detailsNotFound ? 'Проект не найден' : 'Не удалось загрузить проект'}
            </h1>
            <p className="mt-2 text-gray-600">
              {detailsNotFound ? 'Проект не существует или у вас нет к нему доступа.' : detailsError}
            </p>
          </div>
          {!detailsNotFound && id && (
            <Button type="button" className="mt-4" onClick={() => void dispatch(loadProjectDetails(id))}>
              Повторить
            </Button>
          )}
        </Card>
      </div>
    )
  }

  const tools = [
    {
      name: 'Ключевые слова',
      description: 'Исследуйте ключевые фразы и ведите список отслеживаемых запросов для проекта.',
      link: `/dashboard/keywords?project=${project.id}`,
    },
    {
      name: 'Оптимизация контента',
      description: 'Оцените текст страницы и получите практические рекомендации по улучшению.',
      link: `/dashboard/content?project=${project.id}`,
    },
    {
      name: 'Ссылки',
      description: 'Просмотрите исходящие ссылки страниц проекта и оцените их качество.',
      link: `/dashboard/backlinks?project=${project.id}`,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <Link to="/dashboard/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
          Назад к проектам
        </Link>
        <h1 className="mt-2 text-3xl font-bold text-gray-900">{project.name}</h1>
        <a
          href={project.url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-blue-600 hover:text-blue-700"
        >
          {project.url}
        </a>
      </div>

      {claimRejected && (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {CLAIM_REJECTED_MESSAGES[claimRejected] ?? 'Проект создан, но публичный аудит к нему не присоединён.'}
        </div>
      )}

      {project.status === 'scope_review' && (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Для проекта не удалось определить область сайта. Он доступен для просмотра, но расширенный аудит и подтверждение
          владения недоступны.
        </div>
      )}

      <Card className="overflow-hidden p-0">
        <div className="grid gap-2 p-2 md:grid-cols-3">
          {projectTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => handleSelectTab(tab.id)}
              aria-pressed={activeTab === tab.id}
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
          {project.description && <Card className="p-6 text-gray-700">{project.description}</Card>}

          <ProjectOwnership
            key={project.id}
            projectId={project.id}
            summary={project.verification}
            scope={project.scope}
            scopeReview={project.status === 'scope_review'}
          />

          <ProjectFFScore score={project.ffScore} />

          <Card className="p-6">
            <h2 className="text-xl font-semibold text-gray-900">Детали проекта</h2>
            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Статус</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectStatus(project.status)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Создан</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(project.createdAt)}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-4">
                <p className="text-xs uppercase tracking-wide text-gray-400">Обновлён</p>
                <p className="mt-2 text-lg font-semibold text-gray-900">{formatProjectDate(project.updatedAt)}</p>
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
        <ProjectIntegrationsTab projectId={project.id} projectUrl={project.url} />
      )}

      {activeTab === 'audit' && (
        <ProjectAuditTab
          key={project.id}
          projectId={project.id}
          projectUrl={project.url}
          scopeReview={project.status === 'scope_review'}
          onOpenOwnership={() => handleSelectTab('overview')}
        />
      )}
    </div>
  )
}

export default ProjectDetail
