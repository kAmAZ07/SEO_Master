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
      setError(getApiErrorMessage(requestError, 'Failed to load content analysis history.'))
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
      setError(getApiErrorMessage(requestError, 'Failed to analyze the content.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Content optimization</h1>
        <p className="mt-1 text-gray-600">Review content quality, keyword usage, and structural hints before publishing changes.</p>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Analyze a page draft</h2>
        <form onSubmit={handleAnalyze} className="mt-4 space-y-4">
          <Input
            label="Page URL"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/page"
            required
          />
          <Input
            label="Target keyword"
            value={targetKeyword}
            onChange={(event) => setTargetKeyword(event.target.value)}
            placeholder="technical SEO audit"
            required
          />
          <div className="space-y-1.5">
            <label htmlFor="content-body" className="block text-sm font-medium text-gray-700">
              Content draft
            </label>
            <textarea
              id="content-body"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              className="min-h-[220px] w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 transition-colors focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder="Paste the article or landing-page copy you want to review."
              required
            />
          </div>
          <Button type="submit" disabled={loading || !content.trim()}>
            {loading ? 'Analyzing...' : 'Analyze content'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {analysis && (
        <Card className="p-6">
          <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-gray-900">Analysis result</h2>
              <p className="mt-1 text-sm text-gray-600">The score combines content depth, keyword coverage, structure, and uniqueness.</p>
            </div>
            <div className="text-right">
              <p className="text-xs uppercase tracking-wide text-gray-400">Score</p>
              <p className="text-4xl font-bold text-gray-900">{analysis.score}/100</p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Word count</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.wordCount}</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Keyword density</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.keywordDensity}%</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-xs uppercase tracking-wide text-gray-400">Estimated uniqueness</p>
              <p className="mt-2 text-2xl font-bold text-gray-900">{analysis.uniqueness}%</p>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
            <div>
              <h3 className="mb-3 text-lg font-semibold text-gray-900">Recommendations</h3>
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
                <p className="text-sm text-gray-500">No major recommendations were generated for this draft.</p>
              )}
            </div>
            <div>
              <h3 className="mb-3 text-lg font-semibold text-gray-900">Detected issues</h3>
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
                <p className="text-sm text-gray-500">No critical issues detected in the current draft.</p>
              )}
            </div>
          </div>
        </Card>
      )}

      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Recent analyses</h2>
            <p className="mt-1 text-sm text-gray-600">Stored in-memory for the current project bucket in local dev mode.</p>
          </div>
          <Button type="button" variant="outline" onClick={() => void loadHistory()} disabled={loading}>
            Refresh
          </Button>
        </div>

        {history.length ? (
          <div className="space-y-3">
            {history.map((item) => (
              <div key={item.id} className="flex flex-col gap-2 rounded-lg border border-gray-200 px-4 py-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-medium text-gray-900">{item.url || 'Untitled draft'}</p>
                  <p className="mt-1 text-sm text-gray-600">Keyword: {item.keyword || 'Not specified'}</p>
                  <p className="mt-1 text-xs text-gray-400">{new Date(item.analyzedAt).toLocaleString()}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs uppercase tracking-wide text-gray-400">Score</p>
                  <p className="text-2xl font-bold text-gray-900">{item.score}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">No analysis history yet.</p>
        )}
      </Card>
    </div>
  )
}

export default ContentOptimization
