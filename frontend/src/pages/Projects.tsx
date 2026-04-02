import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { createProject, deleteProject, fetchProjects } from '../store/slices/dashboardSlice'
import { Project } from '../types/dashboard'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

const Projects = () => {
  const dispatch = useAppDispatch()
  const { projects, loading } = useAppSelector((state) => state.dashboard)
  const [showModal, setShowModal] = useState(false)
  const [newProject, setNewProject] = useState({ name: '', url: '', description: '' })

  useEffect(() => {
    dispatch(fetchProjects())
  }, [dispatch])

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault()
    await dispatch(createProject(newProject))
    setNewProject({ name: '', url: '', description: '' })
    setShowModal(false)
  }

  const handleDeleteProject = async (id: string) => {
    if (window.confirm('Are you sure you want to delete this project?')) {
      await dispatch(deleteProject(id))
    }
  }

  if (loading && !projects.length) {
    return <Loader />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Projects</h1>
          <p className="mt-1 text-gray-600">Manage your SEO projects and workspaces.</p>
        </div>
        <Button onClick={() => setShowModal(true)}>+ Create project</Button>
      </div>

      {projects.length > 0 ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {projects.map((project: Project) => (
            <Card key={project.id} className="transition-shadow hover:shadow-lg">
              <div className="space-y-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-gray-900">{project.name}</h3>
                    <p className="mt-1 text-sm text-gray-600">{project.url}</p>
                    {project.description && <p className="mt-2 text-sm text-gray-500">{project.description}</p>}
                  </div>
                  <button onClick={() => handleDeleteProject(project.id)} className="text-sm text-red-600 hover:text-red-700">
                    Delete
                  </button>
                </div>

                <div className="flex items-center justify-between border-t border-gray-200 pt-4">
                  <span className="text-xs text-gray-500">
                    Created: {project.createdAt ? new Date(project.createdAt).toLocaleDateString('ru-RU') : '-'}
                  </span>
                  <Link to={`/app/projects/${project.id}`} className="text-sm font-medium text-blue-600 hover:text-blue-700">
                    Open
                  </Link>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="py-12 text-center">
          <p className="mb-4 text-gray-500">No projects yet.</p>
          <Button onClick={() => setShowModal(true)}>Create your first project</Button>
        </Card>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
          <div className="w-full max-w-md rounded-lg bg-white p-6">
            <h2 className="mb-4 text-2xl font-bold text-gray-900">New project</h2>
            <form onSubmit={handleCreateProject} className="space-y-4">
              <Input
                label="Project name"
                value={newProject.name}
                onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                placeholder="My website"
                required
              />
              <Input
                label="Website URL"
                type="url"
                value={newProject.url}
                onChange={(e) => setNewProject({ ...newProject, url: e.target.value })}
                placeholder="https://example.com"
                required
              />
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Description (optional)</label>
                <textarea
                  value={newProject.description}
                  onChange={(e) => setNewProject({ ...newProject, description: e.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-transparent focus:ring-2 focus:ring-blue-500"
                  rows={3}
                  placeholder="Short project description"
                />
              </div>
              <div className="flex gap-3 pt-4">
                <Button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="flex-1 bg-gray-200 text-gray-800 hover:bg-gray-300"
                >
                  Cancel
                </Button>
                <Button type="submit" className="flex-1">
                  Create
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default Projects
