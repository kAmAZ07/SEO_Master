import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { startAudit, fetchAuditHistory } from '../store/slices/auditSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Loader from '../components/ui/Loader';

const Audit = () => {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project');
  
  const dispatch = useAppDispatch();
  const { currentAudit, history, loading } = useAppSelector((state) => state.audit);
  const [url, setUrl] = useState('');

  useEffect(() => {
    dispatch(fetchAuditHistory(projectId ? Number(projectId) : undefined));
  }, [dispatch, projectId]);

  const handleStartAudit = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(startAudit({ url, projectId: projectId ? Number(projectId) : undefined }));
    setUrl('');
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Аудит сайта</h1>
        <p className="text-gray-600 mt-1">Комплексный анализ SEO-показателей вашего сайта</p>
      </div>

      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Запустить новый аудит</h2>
        <form onSubmit={handleStartAudit} className="flex gap-3">
          <Input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Запуск...' : 'Запустить аудит'}
          </Button>
        </form>
      </Card>

      {currentAudit && currentAudit.status === 'in_progress' && (
        <Card>
          <div className="text-center py-8">
            <Loader />
            <p className="text-gray-600 mt-4">Проводим аудит сайта...</p>
            <p className="text-sm text-gray-500 mt-2">Это может занять несколько минут</p>
          </div>
        </Card>
      )}

      {currentAudit && currentAudit.status === 'completed' && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Результаты аудита</h2>
          
          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-700">Общая оценка</span>
              <span className={`text-2xl font-bold ${
                currentAudit.score >= 80 ? 'text-green-600' :
                currentAudit.score >= 50 ? 'text-yellow-600' :
                'text-red-600'
              }`}>
                {currentAudit.score}/100
              </span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all ${
                  currentAudit.score >= 80 ? 'bg-green-600' :
                  currentAudit.score >= 50 ? 'bg-yellow-600' :
                  'bg-red-600'
                }`}
                style={{ width: `${currentAudit.score}%` }}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-green-600">{currentAudit.issues?.passed || 0}</p>
              <p className="text-sm text-gray-600 mt-1">Успешно</p>
            </div>
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-yellow-600">{currentAudit.issues?.warnings || 0}</p>
              <p className="text-sm text-gray-600 mt-1">Предупреждения</p>
            </div>
            <div className="text-center p-4 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-red-600">{currentAudit.issues?.errors || 0}</p>
              <p className="text-sm text-gray-600 mt-1">Ошибки</p>
            </div>
          </div>

          {currentAudit.details && (
            <div className="space-y-4">
              <h3 className="font-semibold text-gray-900">Детальные результаты</h3>
              {currentAudit.details.map((detail: any, index: number) => (
                <div key={index} className="border-l-4 pl-4 py-2" style={{
                  borderColor: detail.status === 'error' ? '#ef4444' : detail.status === 'warning' ? '#f59e0b' : '#10b981'
                }}>
                  <h4 className="font-medium text-gray-900">{detail.title}</h4>
                  <p className="text-sm text-gray-600 mt-1">{detail.description}</p>
                  {detail.recommendation && (
                    <p className="text-sm text-blue-600 mt-2">💡 {detail.recommendation}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {history && history.length > 0 && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">История аудитов</h2>
          <div className="space-y-3">
            {history.map((audit: any) => (
              <div key={audit.id} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors">
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{audit.url}</p>
                  <p className="text-sm text-gray-600">
                    {new Date(audit.createdAt).toLocaleString('ru-RU')}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <span className={`text-2xl font-bold ${
                    audit.score >= 80 ? 'text-green-600' :
                    audit.score >= 50 ? 'text-yellow-600' :
                    'text-red-600'
                  }`}>
                    {audit.score}
                  </span>
                  <Button onClick={() => dispatch({ type: 'audit/setCurrentAudit', payload: audit })}>
                    Просмотр
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
};

export default Audit;
