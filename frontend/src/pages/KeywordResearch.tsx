import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { addKeyword, fetchTrackedKeywords, removeKeyword, searchKeywords } from '../store/slices/hitlSlice'
import { KeywordSearchResult, TrackedKeyword } from '../types/keywords'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import Loader from '../components/ui/Loader'

const KeywordResearch = () => {
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')

  const dispatch = useAppDispatch()
  const { searchResults, trackedKeywords, loading } = useAppSelector((state) => state.hitl)
  const [keyword, setKeyword] = useState('')
  const [showResults, setShowResults] = useState(false)

  useEffect(() => {
    if (projectId) {
      dispatch(fetchTrackedKeywords(projectId))
    }
  }, [dispatch, projectId])

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    await dispatch(searchKeywords({ keyword, projectId: projectId || undefined }))
    setShowResults(true)
  }

  const handleAddKeyword = async (keywordData: KeywordSearchResult) => {
    await dispatch(addKeyword({ ...keywordData, projectId: projectId || undefined }))
  }

  const handleRemoveKeyword = async (keywordId: string | number) => {
    if (window.confirm('Remove this keyword from tracking?')) {
      await dispatch(removeKeyword(keywordId))
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Keyword Research</h1>
        <p className="mt-1 text-gray-600">Search and track keywords for your project.</p>
      </div>

      <Card>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">Search keywords</h2>
        <form onSubmit={handleSearch} className="flex gap-3">
          <Input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="Enter a keyword..."
            className="flex-1"
            required
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Searching...' : 'Search'}
          </Button>
        </form>
      </Card>

      {loading && <Loader />}

      {showResults && searchResults.length > 0 && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Search results</h2>
          <div className="space-y-3">
            {searchResults.map((result: KeywordSearchResult, index: number) => (
              <div key={`${result.keyword}-${index}`} className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{result.keyword}</p>
                  <div className="mt-2 flex items-center gap-4 text-sm text-gray-600">
                    <span>Volume: {result.volume?.toLocaleString('ru-RU') || 'N/A'}</span>
                    <span>Difficulty: {result.difficulty || 'N/A'}/100</span>
                    <span>CPC: ${result.cpc || 'N/A'}</span>
                  </div>
                </div>
                <Button onClick={() => handleAddKeyword(result)}>+ Add</Button>
              </div>
            ))}
          </div>
        </Card>
      )}

      {trackedKeywords.length > 0 && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Tracked keywords</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Keyword</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Position</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Volume</th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Change</th>
                  <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Actions</th>
                </tr>
              </thead>
              <tbody>
                {trackedKeywords.map((trackedKeyword: TrackedKeyword) => (
                  <tr key={trackedKeyword.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-900">{trackedKeyword.keyword}</td>
                    <td className="px-4 py-3 text-sm">
                      <span className="font-semibold text-gray-900">{trackedKeyword.position || '-'}</span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {trackedKeyword.volume?.toLocaleString('ru-RU') || '-'}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {trackedKeyword.change !== undefined && (
                        <span
                          className={
                            trackedKeyword.change > 0
                              ? 'text-green-600'
                              : trackedKeyword.change < 0
                                ? 'text-red-600'
                                : 'text-gray-600'
                          }
                        >
                          {trackedKeyword.change > 0 ? '?' : trackedKeyword.change < 0 ? '?' : '-'}{' '}
                          {Math.abs(trackedKeyword.change)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleRemoveKeyword(trackedKeyword.id)}
                        className="text-sm text-red-600 hover:text-red-700"
                      >
                        Remove
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
  )
}

export default KeywordResearch
