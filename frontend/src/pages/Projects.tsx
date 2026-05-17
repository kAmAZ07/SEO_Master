import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { createProject, loadProjects, removeProject } from '../store/slices/dashboardSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'
import { formatProjectStatus } from '../utils/format'

const Projects = () => {
  const dispatch = useAppDispatch()
  const { projects, loading, error } = useAppSelector((state) => state.dashboard)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState({ name: '', url: '', description: '' })
  const [localError, setLocalError] = useState('')

  useEffect(() => {
    void dispatch(loadProjects())
  }, [dispatch])

  const isSubmitDisabled = useMemo(() => !form.name.trim() || !form.url.trim() || loading, [form, loading])

  const handleCreateProject = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalError('')

    try {
      new URL(form.url)
    } catch {
      setLocalError('Введите корректный URL проекта, включая http:// или https://.')
      return
    }

    const result = await dispatch(
      createProject({
        name: form.name.trim(),
        url: form.url.trim(),
        description: form.description.trim() || undefined,
      }),
    )

    if (createProject.fulfilled.match(result)) {
      setForm({ name: '', url: '', description: '' })
      setShowModal(false)
    }
  }

  const handleDeleteProject = async (id: string) => {
    if (!window.confirm('Удалить проект? Это действие нельзя отменить.')) {
      return
    }
    await dispatch(removeProject(id))
  }

  if (loading && !projects.length) {
    return <Loader />
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Проекты</h1>
          <p className="mt-1 text-gray-600">Управляйте сайтами, которые нужно аудировать и оптимизировать.</p>
        </div>
        <Button onClick={() => setShowModal(true)}>Создать проект</Button>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      {projects.length ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => (
            <Card key={project.id} className="flex h-full flex-col justify-between p-6">
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900">{project.name}</h2>
                    <a
                      href={project.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-block text-sm text-blue-600 hover:text-blue-700"
                    >
                      {project.url}
                    </a>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleDeleteProject(project.id)}
                    className="text-sm font-medium text-red-600 hover:text-red-700"
                  >
                    Удалить
                  </button>
                </div>

                {project.description && <p className="text-sm text-gray-600">{project.description}</p>}

                <div className="grid grid-cols-2 gap-3 rounded-lg bg-gray-50 p-4 text-sm text-gray-600">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-gray-400">Статус</p>
                    <p className="mt-1 font-medium text-gray-900">{formatProjectStatus(project.status)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-gray-400">Создан</p>
                    <p className="mt-1 font-medium text-gray-900">
                      {project.createdAt ? new Date(project.createdAt).toLocaleDateString('ru-RU') : 'Недавно'}
                    </p>
                  </div>
                </div>
              </div>

              <div className="mt-6">
                <Link to={`/dashboard/projects/${project.id}`}>
                  <Button className="w-full">Открыть проект</Button>
                </Link>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="p-10 text-center">
          <h2 className="text-xl font-semibold text-gray-900">Проектов пока нет</h2>
          <p className="mt-2 text-gray-600">Создайте первый проект, чтобы начать работу.</p>
          <Button className="mt-6" onClick={() => setShowModal(true)}>
            Создать проект
          </Button>
        </Card>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <Card className="w-full max-w-lg p-6">
            <div className="mb-6 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-2xl font-semibold text-gray-900">Новый проект</h2>
                <p className="mt-1 text-sm text-gray-600">Добавьте сайт, которым хотите управлять в SEO Master.</p>
              </div>
              <button type="button" onClick={() => setShowModal(false)} className="text-sm text-gray-500 hover:text-gray-700">
                Закрыть
              </button>
            </div>

            {(localError || error) && (
              <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {localError || error}
              </div>
            )}

            <form onSubmit={handleCreateProject} className="space-y-4">
              <Input
                label="Название проекта"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Сайт компании"
                required
              />
              <Input
                label="URL сайта"
                type="url"
                value={form.url}
                onChange={(event) => setForm({ ...form, url: event.target.value })}
                placeholder="https://example.com"
                required
              />
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-gray-700" htmlFor="project-description">
                  Описание
                </label>
                <textarea
                  id="project-description"
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                  className="min-h-[110px] w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 transition-colors focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                  placeholder="Заметки о сайте, нише или целях SEO (необязательно)."
                />
              </div>
              <div className="flex gap-3 pt-2">
                <Button type="button" variant="outline" className="flex-1" onClick={() => setShowModal(false)}>
                  Отмена
                </Button>
                <Button type="submit" className="flex-1" disabled={isSubmitDisabled}>
                  {loading ? 'Создаём...' : 'Создать проект'}
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </div>
  )
}

export default Projects
