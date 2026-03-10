import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { fetchDashboardStats } from '../store/slices/dashboardSlice';
import Card from '../components/ui/Card';
import Loader from '../components/ui/Loader';

const Dashboard = () => {
  const dispatch = useAppDispatch();
  const { stats, loading } = useAppSelector((state) => state.dashboard);

  useEffect(() => {
    dispatch(fetchDashboardStats());
  }, [dispatch]);

  if (loading) {
    return <Loader />;
  }

  const statsCards = [
    {
      title: 'Всего проектов',
      value: stats?.totalProjects || 0,
      icon: '📁',
      color: 'blue',
      link: '/projects',
    },
    {
      title: 'Активных проверок',
      value: stats?.activeAudits || 0,
      icon: '🔍',
      color: 'green',
      link: '/audit',
    },
    {
      title: 'Ключевых слов',
      value: stats?.totalKeywords || 0,
      icon: '🔑',
      color: 'purple',
      link: '/keywords',
    },
    {
      title: 'Обратных ссылок',
      value: stats?.totalBacklinks || 0,
      icon: '🔗',
      color: 'orange',
      link: '/backlinks',
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Панель управления</h1>
        <p className="text-gray-600 mt-1">Обзор ваших SEO проектов</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {statsCards.map((stat) => (
          <Link key={stat.title} to={stat.link}>
            <Card className="hover:shadow-lg transition-shadow cursor-pointer">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-600">{stat.title}</p>
                  <p className="text-3xl font-bold text-gray-900 mt-2">{stat.value}</p>
                </div>
                <div className={`text-5xl opacity-20`}>{stat.icon}</div>
              </div>
            </Card>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Последние проекты</h2>
          {stats?.recentProjects && stats.recentProjects.length > 0 ? (
            <div className="space-y-3">
              {stats.recentProjects.map((project: any) => (
                <div
                  key={project.id}
                  className="flex items-center justify-between p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors"
                >
                  <div>
                    <h3 className="font-medium text-gray-900">{project.name}</h3>
                    <p className="text-sm text-gray-600">{project.url}</p>
                  </div>
                  <Link
                    to={`/projects/${project.id}`}
                    className="text-blue-600 hover:text-blue-700 text-sm font-medium"
                  >
                    Открыть
                  </Link>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-500 text-center py-8">Нет проектов</p>
          )}
        </Card>

        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Последние аудиты</h2>
          {stats?.recentAudits && stats.recentAudits.length > 0 ? (
            <div className="space-y-3">
              {stats.recentAudits.map((audit: any) => (
                <div
                  key={audit.id}
                  className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                >
                  <div>
                    <h3 className="font-medium text-gray-900">{audit.url}</h3>
                    <p className="text-sm text-gray-600">Оценка: {audit.score}/100</p>
                  </div>
                  <span
                    className={`px-3 py-1 rounded-full text-xs font-medium ${
                      audit.status === 'completed'
                        ? 'bg-green-100 text-green-800'
                        : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {audit.status === 'completed' ? 'Завершён' : 'В процессе'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-500 text-center py-8">Нет аудитов</p>
          )}
        </Card>
      </div>
    </div>
  );
};

export default Dashboard;
