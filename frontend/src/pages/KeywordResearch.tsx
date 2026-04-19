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
      setError(getApiErrorMessage(requestError, 'Failed to load tracked keywords.'))
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
      setError(getApiErrorMessage(requestError, 'Failed to search for keywords.'))
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
      setSuccess(`Now tracking "${item.keyword}".`)
      await loadTrackedKeywords()
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Failed to save the keyword.'))
      setLoading(false)
    }
  }

  const handleRemoveKeyword = async (keywordId: string) => {
    if (!window.confirm('Remove this keyword from tracking?')) {
      return
    }

    setLoading(true)
    setError('')
    setSuccess('')

    try {
      await api.delete(`/keywords/tracked/${keywordId}`)
      setTrackedKeywords((current) => current.filter((item) => item.id !== keywordId))
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Failed to remove the keyword.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Keyword research</h1>
        <p className="mt-1 text-gray-600">Search for keyword ideas, compare basic metrics, and keep a tracked shortlist.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {success && <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">{success}</div>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Search for keyword ideas</h2>
        <form onSubmit={handleSearch} className="mt-4 flex flex-col gap-3 md:flex-row">
          <Input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="Enter a seed keyword"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading || !keyword.trim()}>
            {loading ? 'Searching...' : 'Search'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {searchResults.length > 0 && (
        <Card className="p-6">
          <h2 className="text-xl font-semibold text-gray-900">Suggested keywords</h2>
          <div className="mt-4 space-y-3">
            {searchResults.map((item) => (
              <div key={item.id} className="flex flex-col gap-4 rounded-lg border border-gray-200 p-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-medium text-gray-900">{item.keyword}</p>
                  <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
                    <span>Volume: {item.volume?.toLocaleString() ?? 'N/A'}</span>
                    <span>Difficulty: {item.difficulty ?? 'N/A'}</span>
                    <span>CPC: {item.cpc ? `$${item.cpc}` : 'N/A'}</span>
                  </div>
                </div>
                <Button type="button" onClick={() => void handleTrackKeyword(item)}>
                  Track keyword
                </Button>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Tracked keywords</h2>
            <p className="mt-1 text-sm text-gray-600">Saved items are persisted as project-linked semantic events.</p>
          </div>
          <Button type="button" variant="outline" onClick={() => void loadTrackedKeywords()} disabled={loading}>
            Refresh
          </Button>
        </div>

        {hasTrackedKeywords ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr className="border-b border-gray-200 text-left text-sm text-gray-500">
                  <th className="px-4 py-3 font-medium">Keyword</th>
                  <th className="px-4 py-3 font-medium">Position</th>
                  <th className="px-4 py-3 font-medium">Volume</th>
                  <th className="px-4 py-3 font-medium">Change</th>
                  <th className="px-4 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {trackedKeywords.map((item) => (
                  <tr key={item.id} className="border-b border-gray-100 text-sm text-gray-700">
                    <td className="px-4 py-3 font-medium text-gray-900">{item.keyword}</td>
                    <td className="px-4 py-3">{item.position ?? '-'}</td>
                    <td className="px-4 py-3">{item.volume?.toLocaleString() ?? '-'}</td>
                    <td className="px-4 py-3">{typeof item.change === 'number' ? item.change : '-'}</td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={() => void handleRemoveKeyword(item.id)} className="font-medium text-red-600 hover:text-red-700">
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-500">No keywords tracked yet.</p>
        )}
      </Card>
    </div>
  )
}

export default KeywordResearch
