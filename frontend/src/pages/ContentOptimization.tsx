import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { analyzeContent, fetchOptimizedPages } from '../store/slices/dashboardSlice'
import { ContentIssue, ContentRecommendation, OptimizedPage } from '../types/dashboard'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

const ContentOptimization = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')

  const dispatch = useAppDispatch()
  const { contentAnalysis, optimizedPages, loading } = useAppSelector((state) => state.dashboard)
  const [url, setUrl] = useState('')
  const [targetKeyword, setTargetKeyword] = useState('')
  const [content, setContent] = useState('')

  useEffect(() => {
    if (projectId) {
      dispatch(fetchOptimizedPages(projectId))
    }
  }, [dispatch, projectId])

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault()
    await dispatch(analyzeContent({ url, targetKeyword, content, projectId: projectId || undefined }))
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Content Optimization</h1>
        <p className="mt-1 text-gray-600">Analyze and improve page content for search engines.</p>
      </div>

      <Card>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Analyze content</h2>
        <form onSubmit={handleAnalyze} className="space-y-4">
          <Input
            label="Page URL"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/page"
            required
          />
          <Input
            label="Target keyword"
            value={targetKeyword}
            onChange={(e) => setTargetKeyword(e.target.value)}
            placeholder="target keyword"
            required
          />
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Content to analyze</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-transparent focus:ring-2 focus:ring-blue-500"
              rows={8}
              placeholder="Paste page content here..."
              required
            />
          </div>
          <Button type="submit" disabled={loading}>
            {loading ? 'Analyzing...' : 'Analyze'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {contentAnalysis && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Analysis results</h2>

          <div className="mb-6">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">SEO content score</span>
              <span
                className={`text-2xl font-bold ${
                  contentAnalysis.score >= 80
                    ? 'text-green-600'
                    : contentAnalysis.score >= 50
                      ? 'text-yellow-600'
                      : 'text-red-600'
                }`}
              >
                {contentAnalysis.score}/100
              </span>
            </div>
            <div className="h-3 w-full rounded-full bg-gray-200">
              <div
                className={`h-3 rounded-full transition-all ${
                  contentAnalysis.score >= 80
                    ? 'bg-green-600'
                    : contentAnalysis.score >= 50
                      ? 'bg-yellow-600'
                      : 'bg-red-600'
                }`}
                style={{ width: `${contentAnalysis.score}%` }}
              />
            </div>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-sm text-gray-600">Word count</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{contentAnalysis.wordCount}</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-sm text-gray-600">Keyword density</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{contentAnalysis.keywordDensity}%</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4">
              <p className="text-sm text-gray-600">Uniqueness</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{contentAnalysis.uniqueness}%</p>
            </div>
          </div>

          {contentAnalysis.recommendations.length > 0 && (
            <div className="space-y-3">
              <h3 className="font-semibold text-gray-900">Recommendations</h3>
              {contentAnalysis.recommendations.map((recommendation: ContentRecommendation, index: number) => (
                <div key={index} className="flex items-start gap-3 rounded-lg bg-blue-50 p-3">
                  <span className="text-xs font-semibold text-blue-700">TIP</span>
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-900">{recommendation.title}</h4>
                    <p className="mt-1 text-sm text-gray-600">{recommendation.description}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {contentAnalysis.issues.length > 0 && (
            <div className="mt-4 space-y-3">
              <h3 className="font-semibold text-gray-900">Detected issues</h3>
              {contentAnalysis.issues.map((issue: ContentIssue, index: number) => (
                <div key={index} className="flex items-start gap-3 rounded-lg bg-red-50 p-3">
                  <span className="text-xs font-semibold text-red-700">WARN</span>
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-900">{issue.title}</h4>
                    <p className="mt-1 text-sm text-gray-600">{issue.description}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {optimizedPages.length > 0 && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Optimization history</h2>
          <div className="space-y-3">
            {optimizedPages.map((page: OptimizedPage) => (
              <div
                key={page.id}
                className="flex items-center justify-between rounded-lg bg-gray-50 p-4 transition-colors hover:bg-gray-100"
              >
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{page.url}</p>
                  <p className="text-sm text-gray-600">Keyword: {page.keyword}</p>
                  <p className="mt-1 text-xs text-gray-500">{new Date(page.analyzedAt).toLocaleString('ru-RU')}</p>
                </div>
                <div className="flex items-center gap-4">
                  <span
                    className={`text-xl font-bold ${
                      page.score >= 80 ? 'text-green-600' : page.score >= 50 ? 'text-yellow-600' : 'text-red-600'
                    }`}
                  >
                    {page.score}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

export default ContentOptimization
