import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../api/axiosConfig'
import { getApiErrorMessage } from '../api/authAPI'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

interface BacklinkRecord {
  id: string
  sourceUrl: string
  targetUrl: string
  type: 'dofollow' | 'nofollow' | string
  domainAuthority?: number
  anchorText?: string
  discoveredAt: string
}

const Backlinks = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project') || undefined

  const [url, setUrl] = useState('')
  const [backlinks, setBacklinks] = useState<BacklinkRecord[]>([])
  const [filterType, setFilterType] = useState<'all' | 'dofollow' | 'nofollow'>('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadBacklinks = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get('/backlinks', { params: projectId ? { projectId } : undefined })
      setBacklinks(response.data || [])
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Failed to load backlinks.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadBacklinks()
  }, [projectId])

  const handleAnalyze = async (event: React.FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    try {
      const response = await api.post('/backlinks/analyze', { url, projectId })
      setBacklinks(response.data || [])
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, 'Failed to analyze backlinks for this URL.'))
    } finally {
      setLoading(false)
    }
  }

  const filteredBacklinks = useMemo(() => {
    if (filterType === 'all') {
      return backlinks
    }
    return backlinks.filter((item) => item.type === filterType)
  }, [backlinks, filterType])

  const averageAuthority = backlinks.length
    ? Math.round(backlinks.reduce((sum, item) => sum + (item.domainAuthority || 0), 0) / backlinks.length)
    : 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Backlinks</h1>
        <p className="mt-1 text-gray-600">Analyze external referring pages and review basic link quality indicators.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Analyze a target URL</h2>
        <form onSubmit={handleAnalyze} className="mt-4 flex flex-col gap-3 md:flex-row">
          <Input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com"
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading || !url.trim()}>
            {loading ? 'Analyzing...' : 'Analyze backlinks'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      <Card className="p-6">
        <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Backlink snapshot</h2>
            <p className="mt-1 text-sm text-gray-600">Filters help you separate dofollow and nofollow links quickly.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {(['all', 'dofollow', 'nofollow'] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setFilterType(type)}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                  filterType === type ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {type === 'all' ? 'All links' : type}
              </button>
            ))}
          </div>
        </div>

        {filteredBacklinks.length ? (
          <div className="space-y-3">
            {filteredBacklinks.map((item) => (
              <div key={item.id} className="rounded-lg border border-gray-200 p-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-2">
                    <a href={item.sourceUrl} target="_blank" rel="noreferrer" className="font-medium text-blue-600 hover:text-blue-700">
                      {item.sourceUrl}
                    </a>
                    <p className="text-sm text-gray-600">Target: {item.targetUrl}</p>
                    <p className="text-sm text-gray-600">Anchor text: {item.anchorText || 'Not provided'}</p>
                    <p className="text-xs text-gray-400">Discovered: {new Date(item.discoveredAt).toLocaleString()}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-lg bg-gray-50 px-4 py-3 text-center">
                      <p className="text-xs uppercase tracking-wide text-gray-400">Type</p>
                      <p className="mt-2 font-semibold text-gray-900">{item.type}</p>
                    </div>
                    <div className="rounded-lg bg-gray-50 px-4 py-3 text-center">
                      <p className="text-xs uppercase tracking-wide text-gray-400">Domain authority</p>
                      <p className="mt-2 font-semibold text-gray-900">{item.domainAuthority ?? '-'}</p>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No backlinks collected yet.</p>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Card className="p-4 text-center">
          <p className="text-xs uppercase tracking-wide text-gray-400">Total links</p>
          <p className="mt-2 text-3xl font-bold text-gray-900">{backlinks.length}</p>
        </Card>
        <Card className="p-4 text-center">
          <p className="text-xs uppercase tracking-wide text-gray-400">Dofollow</p>
          <p className="mt-2 text-3xl font-bold text-gray-900">{backlinks.filter((item) => item.type === 'dofollow').length}</p>
        </Card>
        <Card className="p-4 text-center">
          <p className="text-xs uppercase tracking-wide text-gray-400">Nofollow</p>
          <p className="mt-2 text-3xl font-bold text-gray-900">{backlinks.filter((item) => item.type === 'nofollow').length}</p>
        </Card>
        <Card className="p-4 text-center">
          <p className="text-xs uppercase tracking-wide text-gray-400">Average authority</p>
          <p className="mt-2 text-3xl font-bold text-gray-900">{averageAuthority}</p>
        </Card>
      </div>
    </div>
  )
}

export default Backlinks
