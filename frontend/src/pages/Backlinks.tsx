import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { fetchBacklinks, analyzeBacklink } from '../store/slices/dashboardSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Loader from '../components/ui/Loader';

const Backlinks = () => {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project');
  
  const dispatch = useAppDispatch();
  const { backlinks, loading } = useAppSelector((state) => state.dashboard);
  const [url, setUrl] = useState('');
  const [filterType, setFilterType] = useState<'all' | 'dofollow' | 'nofollow'>('all');

  useEffect(() => {
    if (projectId) {
      dispatch(fetchBacklinks(Number(projectId)));
    }
  }, [dispatch, projectId]);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(analyzeBacklink({ url, projectId: projectId ? Number(projectId) : undefined }));
    setUrl('');
  };

  const filteredBacklinks = backlinks?.filter((link: any) => {
    if (filterType === 'all') return true;
    return link.type === filterType;
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Обратные ссылки</h1>
        <p className="text-gray-600 mt-1">Анализ и мониторинг внешних ссылок на ваш сайт</p>
      </div>

      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Проанализировать обратные ссылки</h2>
        <form onSubmit={handleAnalyze} className="flex gap-3">
          <Input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Анализ...' : 'Проанализировать'}
          </Button>
        </form>
      </Card>

      {backlinks && (
        <Card>
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-semibold text-gray-900">
              Найдено ссылок: {filteredBacklinks?.length || 0}
            </h2>
            <div className="flex gap-2">
              <button
                onClick={() => setFilterType('all')}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  filterType === 'all'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                Все
              </button>
              <button
                onClick={() => setFilterType('dofollow')}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  filterType === 'dofollow'
                    ? 'bg-green-600 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                Dofollow
              </button>
              <button
                onClick={() => setFilterType('nofollow')}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  filterType === 'nofollow'
                    ? 'bg-yellow-600 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                Nofollow
              </button>
            </div>
          </div>

          {loading && <Loader />}

          {filteredBacklinks && filteredBacklinks.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Источник</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Целевая страница</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Тип</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">DA</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Анкор</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Дата</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredBacklinks.map((link: any) => (
                    <tr key={link.id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-3 px-4 text-sm">
                        <a
                          href={link.sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 hover:text-blue-700"
                        >
                          {new URL(link.sourceUrl).hostname}
                        </a>
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 max-w-xs truncate">
                        {link.targetUrl}
                      </td>
                      <td className="py-3 px-4 text-sm">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${
                          link.type === 'dofollow'
                            ? 'bg-green-100 text-green-800'
                            : 'bg-yellow-100 text-yellow-800'
                        }`}>
                          {link.type}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm font-semibold text-gray-900">
                        {link.domainAuthority || '-'}
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 max-w-xs truncate">
                        {link.anchorText || 'Без анкора'}
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-500">
                        {new Date(link.discoveredAt).toLocaleDateString('ru-RU')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-center text-gray-500 py-8">Обратные ссылки не найдены</p>
          )}
        </Card>
      )}

      {backlinks && backlinks.length > 0 && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Статистика</h2>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-blue-600">{backlinks.length}</p>
              <p className="text-sm text-gray-600 mt-1">Всего ссылок</p>
            </div>
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-green-600">
                {backlinks.filter((l: any) => l.type === 'dofollow').length}
              </p>
              <p className="text-sm text-gray-600 mt-1">Dofollow</p>
            </div>
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-yellow-600">
                {backlinks.filter((l: any) => l.type === 'nofollow').length}
              </p>
              <p className="text-sm text-gray-600 mt-1">Nofollow</p>
            </div>
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-purple-600">
                {Math.round(backlinks.reduce((acc: number, l: any) => acc + (l.domainAuthority || 0), 0) / backlinks.length) || 0}
              </p>
              <p className="text-sm text-gray-600 mt-1">Средний DA</p>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
};

export default Backlinks;
