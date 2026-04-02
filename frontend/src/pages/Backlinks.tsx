import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { analyzeBacklink, fetchBacklinks } from '../store/slices/dashboardSlice'
import { Backlink } from '../types/dashboard'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

const safeHostname = (url: string): string => {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

const Backlinks = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')

  const dispatch = useAppDispatch()
  const { backlinks, loading } = useAppSelector((state) => state.dashboard)
  const [url, setUrl] = useState('')
  const [filterType, setFilterType] = useState<'all' | 'dofollow' | 'nofollow'>('all')

  useEffect(() => {
    if (projectId) {
      dispatch(fetchBacklinks(projectId))
    }
  }, [dispatch, projectId])

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault()
    await dispatch(analyzeBacklink({ url, projectId: projectId || undefined }))
    setUrl('')
  }

  const filteredBacklinks = useMemo(
    () => backlinks.filter((link) => filterType === 'all' || link.type === filterType),
    [backlinks, filterType]
  )

  const averageDomainAuthority =
    backlinks.length > 0
      ? Math.round(
          backlinks.reduce((accumulator, link) => accumulator + (link.domainAuthority || 0), 0) /
            backlinks.length
        )
      : 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Backlinks</h1>
        <p className="mt-1 text-gray-600">Analyze and monitor external links pointing to your website.</p>
      </div>

      <Card>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Analyze backlinks</h2>
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
            {loading ? 'Analyzing...' : 'Analyze'}
          </Button>
        </form>
      </Card>

      <Card>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-gray-900">Found links: {filteredBacklinks.length}</h2>
          <div className="flex gap-2">
            <button
              onClick={() => setFilterType('all')}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                filterType === 'all' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              All
            </button>
            <button
              onClick={() => setFilterType('dofollow')}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                filterType === 'dofollow'
                  ? 'bg-green-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              Dofollow
            </button>
            <button
              onClick={() => setFilterType('nofollow')}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
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

        {filteredBacklinks.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Source</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Target page</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Type</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">DA</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Anchor</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Date</th>
                </tr>
              </thead>
              <tbody>
                {filteredBacklinks.map((link: Backlink) => (
                  <tr key={link.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm">
                      <a
                        href={link.sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:text-blue-700"
                      >
                        {safeHostname(link.sourceUrl)}
                      </a>
                    </td>
                    <td className="max-w-xs truncate px-4 py-3 text-sm text-gray-600">{link.targetUrl}</td>
                    <td className="px-4 py-3 text-sm">
                      <span
                        className={`rounded px-2 py-1 text-xs font-medium ${
                          link.type === 'dofollow' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'
                        }`}
                      >
                        {link.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm font-semibold text-gray-900">{link.domainAuthority || '-'}</td>
                    <td className="max-w-xs truncate px-4 py-3 text-sm text-gray-600">{link.anchorText || 'No anchor'}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">
                      {new Date(link.discoveredAt).toLocaleDateString('ru-RU')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-8 text-center text-gray-500">No backlinks found.</p>
        )}
      </Card>

      {backlinks.length > 0 && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Statistics</h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-blue-600">{backlinks.length}</p>
              <p className="mt-1 text-sm text-gray-600">Total links</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-green-600">
                {backlinks.filter((link) => link.type === 'dofollow').length}
              </p>
              <p className="mt-1 text-sm text-gray-600">Dofollow</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-yellow-600">
                {backlinks.filter((link) => link.type === 'nofollow').length}
              </p>
              <p className="mt-1 text-sm text-gray-600">Nofollow</p>
            </div>
            <div className="rounded-lg bg-gray-50 p-4 text-center">
              <p className="text-2xl font-bold text-purple-600">{averageDomainAuthority}</p>
              <p className="mt-1 text-sm text-gray-600">Average DA</p>
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}

export default Backlinks
