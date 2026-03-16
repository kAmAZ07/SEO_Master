import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { analyzeContent, fetchOptimizedPages } from '../store/slices/dashboardSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Loader from '../components/ui/Loader';

const ContentOptimization = () => {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project');
  
  const dispatch = useAppDispatch();
  const { contentAnalysis, optimizedPages, loading } = useAppSelector((state) => state.dashboard);
  const [url, setUrl] = useState('');
  const [targetKeyword, setTargetKeyword] = useState('');
  const [content, setContent] = useState('');

  useEffect(() => {
    if (projectId) {
      dispatch(fetchOptimizedPages(Number(projectId)));
    }
  }, [dispatch, projectId]);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(analyzeContent({ url, targetKeyword, content, projectId: projectId ? Number(projectId) : undefined }));
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Оптимизация контента</h1>
        <p className="text-gray-600 mt-1">Анализ и улучшение текстов для поисковых систем</p>
      </div>

      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Анализировать контент</h2>
        <form onSubmit={handleAnalyze} className="space-y-4">
          <Input
            label="URL страницы"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/page"
            required
          />
          <Input
            label="Целевое ключевое слово"
            value={targetKeyword}
            onChange={(e) => setTargetKeyword(e.target.value)}
            placeholder="ключевое слово"
            required
          />
          <div>
            abel className="block text-sm font-medium text-gray-700 mb-1">
              Текст для анализа
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              rows={8}
              placeholder="Вставьте текст страницы..."
              required
            />
          </div>
          <Button type="submit" disabled={loading}>
            {loading ? 'Анализ...' : 'Проанализировать'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {contentAnalysis && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Результаты анализа</h2>
          
          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-700">SEO-оценка контента</span>
              <span className={`text-2xl font-bold ${
                contentAnalysis.score >= 80 ? 'text-green-600' :
                contentAnalysis.score >= 50 ? 'text-yellow-600' :
                'text-red-600'
              }`}>
                {contentAnalysis.score}/100
              </span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all ${
                  contentAnalysis.score >= 80 ? 'bg-green-600' :
                  contentAnalysis.score >= 50 ? 'bg-yellow-600' :
                  'bg-red-600'
                }`}
                style={{ width: `${contentAnalysis.score}%` }}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div className="p-4 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">Количество слов</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{contentAnalysis.wordCount}</p>
            </div>
            <div className="p-4 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">Плотность ключевых слов</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{contentAnalysis.keywordDensity}%</p>
            </div>
            <div className="p-4 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">Уникальность</p>
              <p className="text-2xl font-bold text-gray-900 mt-1">{contentAnalysis.uniqueness}%</p>
            </div>
          </div>

          {contentAnalysis.recommendations && contentAnalysis.recommendations.length > 0 && (
            <div className="space-y-3">
              <h3 className="font-semibold text-gray-900">Рекомендации по улучшению</h3>
              {contentAnalysis.recommendations.map((rec: any, index: number) => (
                <div key={index} className="flex items-start gap-3 p-3 bg-blue-50 rounded-lg">
                  <span className="text-blue-600 text-xl">💡</span>
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-900">{rec.title}</h4>
                    <p className="text-sm text-gray-600 mt-1">{rec.description}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {contentAnalysis.issues && contentAnalysis.issues.length > 0 && (
            <div className="space-y-3 mt-4">
              <h3 className="font-semibold text-gray-900">Обнаруженные проблемы</h3>
              {contentAnalysis.issues.map((issue: any, index: number) => (
                <div key={index} className="flex items-start gap-3 p-3 bg-red-50 rounded-lg">
                  <span className="text-red-600 text-xl">⚠️</span>
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-900">{issue.title}</h4>
                    <p className="text-sm text-gray-600 mt-1">{issue.description}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {optimizedPages && optimizedPages.length > 0 && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">История оптимизации</h2>
          <div className="space-y-3">
            {optimizedPages.map((page: any) => (
              <div key={page.id} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors">
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{page.url}</p>
                  <p className="text-sm text-gray-600">Ключевое слово: {page.keyword}</p>
                  <p className="text-xs text-gray-500 mt-1">
                    {new Date(page.analyzedAt).toLocaleString('ru-RU')}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <span className={`text-xl font-bold ${
                    page.score >= 80 ? 'text-green-600' :
                    page.score >= 50 ? 'text-yellow-600' :
                    'text-red-600'
                  }`}>
                    {page.score}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
};

export default ContentOptimization;
