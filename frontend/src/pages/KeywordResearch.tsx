import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../api/axiosConfig'
import { getApiErrorMessage } from '../api/authAPI'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

interface KeywordResult {
  id: string
  keyword: string
  volume?: number
  difficulty?: number
  cpc?: number
  position?: number
  change?: number
}

const KeywordResearch = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project') || undefined

  const [keyword, setKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<KeywordResult[]>([])
  const [trackedKeywords, setTrackedKeywords] = useState<KeywordResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const hasTrackedKeywords = useMemo(() => trackedKeywords.length > 0, [trackedKeywords])

  const loadTrackedKeywords = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get('/keywords/tracked', { params: projectId ? { projectId } : undefined })
      setTrackedKeywords(response.data || [])
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось загрузить ключевые слова.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTrackedKeywords()
  }, [projectId])

  const handleSearch = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    setSuccess('')

    try {
      const response = await api.post('/keywords/search', { keyword, projectId })
      setSearchResults(response.data || [])
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось выполнить поиск.'))
    } finally {
      setLoading(false)
    }
  }

  const handleTrackKeyword = async (item: KeywordResult) => {
    setLoading(true)
    setError('')
    setSuccess('')

    try {
      await api.post('/keywords/tracked', {
        keyword: item.keyword,
        projectId,
        volume: item.volume,
        difficulty: item.difficulty,
        cpc: item.cpc,
      })
      setSuccess(`«${item.keyword}» добавлено в отслеживаемые.`)
      await loadTrackedKeywords()
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось сохранить ключевое слово.'))
      setLoading(false)
    }
  }

  const handleRemoveKeyword = async (keywordId: string) => {
    if (!window.confirm('Удалить ключевое слово из отслеживаемых?')) {
      return
    }

    setLoading(true)
    setError('')
    setSuccess('')

    try {
      await api.delete(`/keywords/tracked/${keywordId}`)
      setTrackedKeywords((current) => current.filter((item) => item.id !== keywordId))
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Не удалось удалить ключевое слово.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Ключевые слова</h1>
        <p className="mt-1 text-gray-600">Ищите ключевые слова, сравнивайте метрики и ведите список отслеживаемых запросов.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {success && <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">{success}</div>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Поиск ключевых слов</h2>
        <form onSubmit={handleSearch} className="mt-4 flex flex-col gap-3 md:flex-row">
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="Введите ключевую фразу"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading || !keyword.trim()}>
            {loading ? 'Ищем...' : 'Найти'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {searchResults.length > 0 && (
        <Card className="p-6">
          <h2 className="text-xl font-semibold text-gray-900">Найденные ключевые слова</h2>
          <div className="mt-4 space-y-3">
            {searchResults.map((item) => (
              <div key={item.id} className="flex flex-col gap-4 rounded-lg border border-gray-200 p-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-medium text-gray-900">{item.keyword}</p>
                  <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
                    <span>Объём: {item.volume?.toLocaleString('ru-RU') ?? '—'}</span>
                    <span>Сложность: {item.difficulty ?? '—'}</span>
                    <span>CPC: {item.cpc ? `$${item.cpc}` : '—'}</span>
                  </div>
                </div>
                <Button type="button" onClick={() => void handleTrackKeyword(item)}>
                  Отслеживать
                </Button>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Отслеживаемые ключевые слова</h2>
          </div>
          <Button type="button" variant="outline" onClick={() => void loadTrackedKeywords()} disabled={loading}>
            Обновить
          </Button>
        </div>

        {hasTrackedKeywords ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr className="border-b border-gray-200 text-left text-sm text-gray-500">
                  <th className="px-4 py-3 font-medium">Ключевое слово</th>
                  <th className="px-4 py-3 font-medium">Позиция</th>
                  <th className="px-4 py-3 font-medium">Объём</th>
                  <th className="px-4 py-3 font-medium">Изменение</th>
                  <th className="px-4 py-3 font-medium">Действия</th>
                </tr>
              </thead>
              <tbody>
                {trackedKeywords.map((item) => (
                  <tr key={item.id} className="border-b border-gray-100 text-sm text-gray-700">
                    <td className="px-4 py-3 font-medium text-gray-900">{item.keyword}</td>
                    <td className="px-4 py-3">{item.position ?? '—'}</td>
                    <td className="px-4 py-3">{item.volume?.toLocaleString('ru-RU') ?? '—'}</td>
                    <td className="px-4 py-3">{typeof item.change === 'number' ? item.change : '—'}</td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={() => void handleRemoveKeyword(item.id)} className="font-medium text-red-600 hover:text-red-700">
                        Удалить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-500">Нет отслеживаемых ключевых слов.</p>
        )}
      </Card>
    </div>
  )
}

export default KeywordResearch
