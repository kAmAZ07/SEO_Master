import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../api/axiosConfig'
import { getApiErrorMessage } from '../api/authAPI'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

interface ContentIssue {
  title: string
  description: string
}

interface ContentRecommendation {
  title: string
  description: string
}

interface ContentAnalysisResult {
  score: number
  wordCount: number
  keywordDensity: number
  uniqueness: number
  recommendations: ContentRecommendation[]
  issues: ContentIssue[]
}

interface OptimizedPageHistoryItem {
  id: string
  url: string
  keyword: string
  score: number
  analyzedAt: string
}

const ContentOptimization = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project') || undefined

  const [url, setUrl] = useState('')
  const [targetKeyword, setTargetKeyword] = useState('')
  const [content, setContent] = useState('')
  const [analysis, setAnalysis] = useState<ContentAnalysisResult | null>(null)
  const [history, setHistory] = useState<OptimizedPageHistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = async () => {
    try {
      const response = await api.get('/content/optimized', { params: projectId ? { projectId } : undefined })
      setHistory(response.data || [])
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось загрузить историю анализов.'))
    }
  }

  useEffect(() => {
    void loadHistory()
  }, [projectId])

  const handleAnalyze = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    try {
      const response = await api.post('/content/analyze', {
        url,
        targetKeyword,
        content,
        projectId,
      })
      setAnalysis(response.data)
      await loadHistory()
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось выполнить анализ.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Оптимизация контента</h1>
        <p className="mt-1 text-gray-600">Оцените качество контента, использование ключевых слов и структуру перед публикацией.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Анализ страницы</h2>
        <form onSubmit={handleAnalyze} className="mt-4 space-y-4">
          <Input
            label="URL страницы"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/page"
            required
          />
          <Input
            label="Целевой запрос"
            value={targetKeyword}
            onChange={(event) => setTargetKeyword(event.target.value)}
            placeholder="технический SEO-аудит"
            required
          />
          <div className="space-y-1.5">
            <label htmlFor="content-body" className="block text-sm font-medium text-gray-700">
              Текст страницы
            </label>
            <textarea
              id="content-body"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-[220px] w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 transition-colors focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder="Вставьте текст статьи или лендинга для анализа."
              required
            />
          </div>
          <Button type="submit" disabled={loading || !content.trim()}>
            {loading ? 'Анализируем...' : 'Анализировать'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {analysis && (
        <Card className="p-6">
          <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-900">Результат анализа</h2>
              <p className="mt-1 text-sm text-gray-600">Оценка учитывает глубину контента, охват ключевых слов, структуру и уникальность.</p>
            </div>
            <div className="text-right">
              <p className="text-xs uppercase tracking-wide text-gray-400">Оценка</p>
              <p className="text-4xl font-bold text-gray-900">{analysis.score}/100</p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Количество слов</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.wordCount}</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Плотность ключевых слов</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.keywordDensity}%</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Уникальность</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.uniqueness}%</p>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
            <div>
              <h3 className="mb-3 text-lg font-semibold text-gray-900">Рекомендации</h3>
              {analysis.recommendations.length ? (
                <div className="space-y-3">
                  {analysis.recommendations.map((item, index) => (
                    <div key={`${item.title}-${index}`} className="rounded-lg border border-blue-200 bg-blue-50 p-4">
                      <p className="font-medium text-gray-900">{item.title}</p>
                      <p className="mt-1 text-sm text-gray-700">{item.description}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-500">Рекомендаций не выявлено.</p>
              )}
            </div>
            <div>
              <h3 className="mb-3 text-lg font-semibold text-gray-900">Обнаруженные проблемы</h3>
              {analysis.issues.length ? (
                <div className="space-y-3">
                  {analysis.issues.map((item, index) => (
                    <div key={`${item.title}-${index}`} className="rounded-lg border border-red-200 bg-red-50 p-4">
                      <p className="font-medium text-gray-900">{item.title}</p>
                      <p className="mt-1 text-sm text-gray-700">{item.description}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-500">Критических проблем не обнаружено.</p>
              )}
            </div>
          </div>
        </Card>
      )}

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 className="text-xl font-semibold text-gray-900">История анализов</h2>
          <Button type="button" variant="outline" onClick={() => void loadHistory()} disabled={loading}>
            Обновить
          </Button>
        </div>

        {history.length ? (
          <div className="space-y-3">
            {history.map((item) => (
              <div key={item.id} className="flex flex-col gap-2 rounded-lg border border-gray-200 px-4 py-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-medium text-gray-900">{item.url || 'Без URL'}</p>
                  <p className="mt-1 text-sm text-gray-600">Запрос: {item.keyword || 'Не указан'}</p>
                  <p className="mt-1 text-xs text-gray-400">{new Date(item.analyzedAt).toLocaleString('ru-RU')}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs uppercase tracking-wide text-gray-400">Оценка</p>
                  <p className="text-2xl font-bold text-gray-900">{item.score}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">История анализов пуста.</p>
        )}
      </Card>
    </div>
  )
}

export default ContentOptimization
