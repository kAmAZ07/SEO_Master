import { useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { fetchProjectDetails } from '../store/slices/dashboardSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Loader from '../components/ui/Loader';

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>();
  const dispatch = useAppDispatch();
  const { currentProject, loading } = useAppSelector((state) => state.dashboard);

  useEffect(() => {
    if (id) {
      dispatch(fetchProjectDetails(Number(id)));
    }
  }, [dispatch, id]);

  if (loading || !currentProject) {
    return <Loader />;
  }

  const tools = [
    { name: 'Аудит сайта', description: 'Комплексный анализ SEO', link: `/audit?project=${id}`, icon: '🔍' },
    { name: 'Ключевые слова', description: 'Исследование и отслеживание', link: `/keywords?project=${id}`, icon: '🔑' },
    { name: 'Оптимизация контента', description: 'Улучшение текстов', link: `/content?project=${id}`, icon: '📝' },
    { name: 'Обратные ссылки', description: 'Анализ и мониторинг', link: `/backlinks?project=${id}`, icon: '🔗' },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/projects" className="text-blue-600 hover:text-blue-700 text-sm mb-2 inline-block">
            ← Назад к проектам
          </Link>
          <h1 className="text-3xl font-bold text-gray-900">{currentProject.name}</h1>
          <a
            href={currentProject.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:text-blue-700 mt-1 inline-block"
          >
            {currentProject.url} ↗
          </a>
        </div>
        <Button>Настройки проекта</Button>
      </div>

      {currentProject.description && (
        <Card>
          <p className="text-gray-700">{currentProject.description}</p>
        </Card>
      )}

      <div>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Инструменты SEO</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {tools.map((tool) => (
            <Link key={tool.name} to={tool.link}>
              <Card className="hover:shadow-lg transition-shadow cursor-pointer h-full">
                <div className="flex items-start gap-4">
                  <div className="text-4xl">{tool.icon}</div>
                  <div className="flex-1">
                    <h3 className="font-semibold text-gray-900">{tool.name}</h3>
                    <p className="text-sm text-gray-600 mt-1">{tool.description}</p>
                  </div>
                  <span className="text-blue-600">→</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      </div>

      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Статистика проекта</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center">
            <p className="text-3xl font-bold text-blue-600">{currentProject.stats?.audits || 0}</p>
            <p className="text-sm text-gray-600 mt-1">Аудитов</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-green-600">{currentProject.stats?.keywords || 0}</p>
            <p className="text-sm text-gray-600 mt-1">Ключевых слов</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-purple-600">{currentProject.stats?.pages || 0}</p>
            <p className="text-sm text-gray-600 mt-1">Страниц</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-orange-600">{currentProject.stats?.backlinks || 0}</p>
            <p className="text-sm text-gray-600 mt-1">Обратных ссылок</p>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default ProjectDetail;
