import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { searchKeywords, fetchTrackedKeywords, addKeyword, removeKeyword } from '../store/slices/hitlSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';
import Loader from '../components/ui/Loader';

const KeywordResearch = () => {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get('project');
  
  const dispatch = useAppDispatch();
  const { searchResults, trackedKeywords, loading } = useAppSelector((state) => state.hitl);
  const [keyword, setKeyword] = useState('');
  const [showResults, setShowResults] = useState(false);

  useEffect(() => {
    if (projectId) {
      dispatch(fetchTrackedKeywords(Number(projectId)));
    }
  }, [dispatch, projectId]);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(searchKeywords({ keyword, projectId: projectId ? Number(projectId) : undefined }));
    setShowResults(true);
  };

  const handleAddKeyword = async (keywordData: any) => {
    await dispatch(addKeyword({ ...keywordData, projectId: projectId ? Number(projectId) : undefined }));
  };

  const handleRemoveKeyword = async (keywordId: number) => {
    if (window.confirm('Удалить это ключевое слово из отслеживания?')) {
      await dispatch(removeKeyword(keywordId));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Исследование ключевых слов</h1>
        <p className="text-gray-600 mt-1">Поиск и анализ ключевых слов для вашего проекта</p>
      </div>

      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Поиск ключевых слов</h2>
        <form onSubmit={handleSearch} className="flex gap-3">
          <Input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="Введите ключевое слово..."
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Поиск...' : 'Найти'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {showResults && searchResults && searchResults.length > 0 && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Результаты поиска</h2>
          <div className="space-y-3">
            {searchResults.map((result: any, index: number) => (
              <div key={index} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{result.keyword}</p>
                  <div className="flex items-center gap-4 mt-2 text-sm text-gray-600">
                    <span>Объём: {result.volume?.toLocaleString('ru-RU') || 'N/A'}</span>
                    <span>Сложность: {result.difficulty || 'N/A'}/100</span>
                    <span>CPC: ${result.cpc || 'N/A'}</span>
                  </div>
                </div>
                <Button onClick={() => handleAddKeyword(result)}>
                  + Добавить
                </Button>
              </div>
            ))}
          </div>
        </Card>
      )}

      {trackedKeywords && trackedKeywords.length > 0 && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Отслеживаемые ключевые слова</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Ключевое слово</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Позиция</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Объём</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-gray-700">Изменение</th>
                  <th className="text-right py-3 px-4 text-sm font-medium text-gray-700">Действия</th>
                </tr>
              </thead>
              <tbody>
                {trackedKeywords.map((kw: any) => (
                  <tr key={kw.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-3 px-4 text-sm text-gray-900">{kw.keyword}</td>
                    <td className="py-3 px-4 text-sm">
                      <span className="font-semibold text-gray-900">{kw.position || '-'}</span>
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-600">
                      {kw.volume?.toLocaleString('ru-RU') || '-'}
                    </td>
                    <td className="py-3 px-4 text-sm">
                      {kw.change && (
                        <span className={kw.change > 0 ? 'text-green-600' : kw.change < 0 ? 'text-red-600' : 'text-gray-600'}>
                          {kw.change > 0 ? '↑' : kw.change < 0 ? '↓' : '–'} {Math.abs(kw.change)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => handleRemoveKeyword(kw.id)}
                        className="text-red-600 hover:text-red-700 text-sm"
                      >
                        Удалить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};

export default KeywordResearch;
