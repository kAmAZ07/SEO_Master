import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { fetchProjects, createProject, deleteProject } from '../store/slices/dashboardSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Loader from '../components/ui/Loader';

const Projects = () => {
  const dispatch = useAppDispatch();
  const { projects, loading } = useAppSelector((state) => state.dashboard);
  const [showModal, setShowModal] = useState(false);
  const [newProject, setNewProject] = useState({ name: '', url: '', description: '' });

  useEffect(() => {
    dispatch(fetchProjects());
  }, [dispatch]);

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(createProject(newProject));
    setNewProject({ name: '', url: '', description: '' });
    setShowModal(false);
  };

  const handleDeleteProject = async (id: number) => {
    if (window.confirm('Вы уверены, что хотите удалить этот проект?')) {
      await dispatch(deleteProject(id));
    }
  };

  if (loading && !projects.length) {
    return <Loader />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Мои проекты</h1>
          <p className="text-gray-600 mt-1">Управление вашими SEO проектами</p>
        </div>
        <Button onClick={() => setShowModal(true)}>
          + Создать проект
        </Button>
      </div>

      {projects && projects.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((project: any) => (
            <Card key={project.id} className="hover:shadow-lg transition-shadow">
              <div className="space-y-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-gray-900">{project.name}</h3>
                    <p className="text-sm text-gray-600 mt-1">{project.url}</p>
                    {project.description && (
                      <p className="text-sm text-gray-500 mt-2">{project.description}</p>
                    )}
                  </div>
                  <button
                    onClick={() => handleDeleteProject(project.id)}
                    className="text-red-600 hover:text-red-700 text-sm"
                  >
                    ✕
                  </button>
                </div>
                
                <div className="flex items-center justify-between pt-4 border-t border-gray-200">
                  <span className="text-xs text-gray-500">
                    Создан: {new Date(project.createdAt).toLocaleDateString('ru-RU')}
                  </span>
                  <Link
                    to={`/projects/${project.id}`}
                    className="text-blue-600 hover:text-blue-700 text-sm font-medium"
                  >
                    Открыть →
                  </Link>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="text-center py-12">
          <p className="text-gray-500 mb-4">У вас пока нет проектов</p>
          <Button onClick={() => setShowModal(true)}>Создать первый проект</Button>
        </Card>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-md">
            <h2 className="text-2xl font-bold text-gray-900 mb-4">Новый проект</h2>
            <form onSubmit={handleCreateProject} className="space-y-4">
              <Input
                label="Название проекта"
                value={newProject.name}
                onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                placeholder="Мой сайт"
                required
              />
              <Input
                label="URL сайта"
                type="url"
                value={newProject.url}
                onChange={(e) => setNewProject({ ...newProject, url: e.target.value })}
                placeholder="https://example.com"
                required
              />
              <div>
                abel className="block text-sm font-medium text-gray-700 mb-1">
                  Описание (опционально)
                </label>
                <textarea
                  value={newProject.description}
                  onChange={(e) => setNewProject({ ...newProject, description: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  rows={3}
                  placeholder="Краткое описание проекта"
                />
              </div>
              <div className="flex gap-3 pt-4">
                <Button type="button" onClick={() => setShowModal(false)} className="flex-1 bg-gray-200 text-gray-800 hover:bg-gray-300">
                  Отмена
                </Button>
                <Button type="submit" className="flex-1">
                  Создать
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default Projects;
