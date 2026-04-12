import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { loadProjectDetails } from '../store/slices/dashboardSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Loader from '../components/ui/Loader'
import Input from '../components/ui/Input'
import {
  fetchProjectIntegrations,
  revokeIntegration,
  saveTildaIntegration,
  saveWordpressIntegration,
} from '../api/integrationsAPI'
import { getApiErrorMessage } from '../api/authAPI'
import type { ProjectIntegrationStatus } from '../types/integrations'

const ProjectDetail = () => {
  const { id } = useParams<{ id: string }>()
  const dispatch = useAppDispatch()
  const { currentProject, loading, error } = useAppSelector((state) => state.dashboard)
  const [integrationTab, setIntegrationTab] = useState<'tilda' | 'wordpress'>('tilda')
  const [integrationsLoading, setIntegrationsLoading] = useState(false)
  const [integrationsError, setIntegrationsError] = useState<string | null>(null)
  const [integrationsMessage, setIntegrationsMessage] = useState<string | null>(null)
  const [savingPlatform, setSavingPlatform] = useState<'tilda' | 'wordpress' | null>(null)
  const [disconnectingPlatform, setDisconnectingPlatform] = useState<'tilda' | 'wordpress' | null>(null)
  const [integrations, setIntegrations] = useState<Record<string, ProjectIntegrationStatus>>({})
  const [tildaForm, setTildaForm] = useState({
    publicKey: '',
    secretKey: '',
    projectId: '',
  })
  const [wordpressForm, setWordpressForm] = useState({
    baseUrl: '',
    hmacSecret: '',
  })

  useEffect(() => {
    if (id) {
      void dispatch(loadProjectDetails(id))
    }
  }, [dispatch, id])

  useEffect(() => {
    if (!id) {
      return
    }

    const loadIntegrations = async () => {
      setIntegrationsLoading(true)
      setIntegrationsError(null)

      try {
        const response = await fetchProjectIntegrations(id)
        setIntegrations(
          response.items.reduce<Record<string, ProjectIntegrationStatus>>((acc, item) => {
            acc[item.platform] = item
            return acc
          }, {}),
        )
      } catch (integrationError) {
        setIntegrationsError(getApiErrorMessage(integrationError, 'Failed to load integration settings.'))
      } finally {
        setIntegrationsLoading(false)
      }
    }

    void loadIntegrations()
  }, [id])

  useEffect(() => {
    if (!wordpressForm.baseUrl && currentProject?.url) {
      setWordpressForm((prev) => ({ ...prev, baseUrl: currentProject.url }))
    }
  }, [currentProject?.url, wordpressForm.baseUrl])

  if (loading && !currentProject) {
    return <Loader />
  }

  if (!currentProject) {
    return (
      <div className="space-y-4">
        <Link to="/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
          Back to projects
        </Link>
        <Card className="p-6">
          <h1 className="text-2xl font-semibold text-gray-900">Project not found</h1>
          <p className="mt-2 text-gray-600">{error || 'This project is unavailable or you no longer have access to it.'}</p>
        </Card>
      </div>
    )
  }

  const tools = [
    {
      name: 'Technical audit',
      description: 'Run a crawl and review detailed issues, CWV checks, and scoring.',
      link: `/audit?project=${currentProject.id}`,
    },
    {
      name: 'Keyword research',
      description: 'Generate ideas and maintain a tracked list for this project.',
      link: `/keywords?project=${currentProject.id}`,
    },
    {
      name: 'Content optimization',
      description: 'Analyze page copy and get practical recommendations.',
      link: `/content?project=${currentProject.id}`,
    },
    {
      name: 'Backlink review',
      description: 'Inspect discovered referring pages and basic backlink quality signals.',
      link: `/backlinks?project=${currentProject.id}`,
    },
  ]

  const tildaIntegration = integrations.tilda
  const wordpressIntegration = integrations.wordpress

  const integrationTabClass = (tab: 'tilda' | 'wordpress') =>
    `px-4 py-2 font-medium transition-colors ${
      integrationTab === tab ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-600 hover:text-gray-900'
    }`

  const handleSaveTilda = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!id) {
      return
    }

    setSavingPlatform('tilda')
    setIntegrationsError(null)
    setIntegrationsMessage(null)

    try {
      const saved = await saveTildaIntegration(id, {
        publicKey: tildaForm.publicKey,
        secretKey: tildaForm.secretKey,
        projectId: tildaForm.projectId,
      })
      setIntegrations((prev) => ({ ...prev, tilda: saved }))
      setIntegrationsMessage('Tilda integration connected.')
      setTildaForm((prev) => ({ ...prev, secretKey: '' }))
    } catch (integrationError) {
      setIntegrationsError(getApiErrorMessage(integrationError, 'Failed to connect Tilda integration.'))
    } finally {
      setSavingPlatform(null)
    }
  }

  const handleSaveWordpress = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!id) {
      return
    }

    setSavingPlatform('wordpress')
    setIntegrationsError(null)
    setIntegrationsMessage(null)

    try {
      const saved = await saveWordpressIntegration(id, {
        baseUrl: wordpressForm.baseUrl,
        hmacSecret: wordpressForm.hmacSecret,
      })
      setIntegrations((prev) => ({ ...prev, wordpress: saved }))
      setIntegrationsMessage('WordPress integration connected.')
      setWordpressForm((prev) => ({ ...prev, hmacSecret: '' }))
    } catch (integrationError) {
      setIntegrationsError(getApiErrorMessage(integrationError, 'Failed to connect WordPress integration.'))
    } finally {
      setSavingPlatform(null)
    }
  }

  const handleDisconnect = async (platform: 'tilda' | 'wordpress') => {
    if (!id) {
      return
    }

    setDisconnectingPlatform(platform)
    setIntegrationsError(null)
    setIntegrationsMessage(null)

    try {
      await revokeIntegration(id, platform)
      setIntegrations((prev) => ({
        ...prev,
        [platform]: {
          platform,
          connected: false,
          status: 'not_configured',
        },
      }))
      setIntegrationsMessage(`${platform === 'tilda' ? 'Tilda' : 'WordPress'} integration disconnected.`)
    } catch (integrationError) {
      setIntegrationsError(getApiErrorMessage(integrationError, 'Failed to revoke integration.'))
    } finally {
      setDisconnectingPlatform(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <Link to="/projects" className="text-sm font-medium text-blue-600 hover:text-blue-700">
            Back to projects
          </Link>
          <h1 className="mt-2 text-3xl font-bold text-gray-900">{currentProject.name}</h1>
          <a href={currentProject.url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-blue-600 hover:text-blue-700">
            {currentProject.url}
          </a>
        </div>
        <Link to={`/audit?project=${currentProject.id}`}>
          <Button>Run audit</Button>
        </Link>
      </div>

      {currentProject.description && <Card className="p-6 text-gray-700">{currentProject.description}</Card>}

      <Card className="p-6">
        <h2 className="text-xl font-semibold text-gray-900">Project details</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Status</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">{currentProject.status}</p>
          </div>
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Created</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">
              {currentProject.createdAt ? new Date(currentProject.createdAt).toLocaleString() : 'Unavailable'}
            </p>
          </div>
          <div className="rounded-lg bg-gray-50 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-400">Updated</p>
            <p className="mt-2 text-lg font-semibold text-gray-900">
              {currentProject.updatedAt ? new Date(currentProject.updatedAt).toLocaleString() : 'Unavailable'}
            </p>
          </div>
        </div>
      </Card>

      <Card className="p-6">
        <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Integrations</h2>
            <p className="mt-1 text-sm text-gray-600">
              Store adapter credentials per project. Secrets are encrypted and shown only as masked hints.
            </p>
          </div>
          {integrationsLoading && <p className="text-sm text-gray-500">Refreshing...</p>}
        </div>

        <div className="mt-4 flex gap-4 border-b border-gray-200">
          <button type="button" onClick={() => setIntegrationTab('tilda')} className={integrationTabClass('tilda')}>
            Tilda
          </button>
          <button
            type="button"
            onClick={() => setIntegrationTab('wordpress')}
            className={integrationTabClass('wordpress')}
          >
            WordPress
          </button>
        </div>

        {integrationsError && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {integrationsError}
          </div>
        )}

        {integrationsMessage && (
          <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            {integrationsMessage}
          </div>
        )}

        {integrationTab === 'tilda' && (
          <div className="mt-6">
            {tildaIntegration?.connected ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-2">
                    <p className="text-lg font-semibold text-emerald-900">Tilda connected</p>
                    <p className="text-sm text-emerald-800">Public key hint: {tildaIntegration.hint || 'Hidden'}</p>
                    <p className="text-sm text-emerald-800">
                      Project ID: {tildaIntegration.projectIdentifier || 'Not specified'}
                    </p>
                    <p className="text-sm text-emerald-800">
                      Connected:{' '}
                      {tildaIntegration.connectedAt
                        ? new Date(tildaIntegration.connectedAt).toLocaleString()
                        : 'Unavailable'}
                    </p>
                    <p className="text-sm text-emerald-800">
                      Page mappings: {tildaIntegration.pageMappingsCount ?? 0}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    onClick={() => void handleDisconnect('tilda')}
                    disabled={disconnectingPlatform === 'tilda'}
                  >
                    {disconnectingPlatform === 'tilda' ? 'Disconnecting...' : 'Disconnect'}
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSaveTilda} className="mt-2 space-y-4">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Input
                    label="Public Key"
                    value={tildaForm.publicKey}
                    onChange={(event) => setTildaForm((prev) => ({ ...prev, publicKey: event.target.value }))}
                    placeholder="xxxxxxxx..."
                    required
                  />
                  <Input
                    label="Secret Key"
                    type="password"
                    value={tildaForm.secretKey}
                    onChange={(event) => setTildaForm((prev) => ({ ...prev, secretKey: event.target.value }))}
                    placeholder="********"
                    required
                  />
                </div>
                <Input
                  label="Tilda Project ID"
                  value={tildaForm.projectId}
                  onChange={(event) => setTildaForm((prev) => ({ ...prev, projectId: event.target.value }))}
                  placeholder="123456"
                  hint="Tilda -> Site Settings -> Export -> API Integration"
                  required
                />
                <Button type="submit" disabled={savingPlatform === 'tilda'}>
                  {savingPlatform === 'tilda' ? 'Connecting...' : 'Connect Tilda'}
                </Button>
              </form>
            )}
          </div>
        )}

        {integrationTab === 'wordpress' && (
          <div className="mt-6">
            {wordpressIntegration?.connected ? (
              <div className="rounded-xl border border-blue-200 bg-blue-50/70 p-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-2">
                    <p className="text-lg font-semibold text-blue-900">WordPress connected</p>
                    <p className="text-sm text-blue-800">Site URL: {wordpressIntegration.siteUrl || 'Unavailable'}</p>
                    <p className="text-sm text-blue-800">Secret hint: {wordpressIntegration.hint || 'Hidden'}</p>
                    <p className="text-sm text-blue-800">
                      Plugin status: {wordpressIntegration.pluginHealth?.status || 'ok'}
                      {wordpressIntegration.pluginHealth?.version
                        ? ` - v${wordpressIntegration.pluginHealth.version}`
                        : ''}
                    </p>
                    <p className="text-sm text-blue-800">
                      Connected:{' '}
                      {wordpressIntegration.connectedAt
                        ? new Date(wordpressIntegration.connectedAt).toLocaleString()
                        : 'Unavailable'}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    onClick={() => void handleDisconnect('wordpress')}
                    disabled={disconnectingPlatform === 'wordpress'}
                  >
                    {disconnectingPlatform === 'wordpress' ? 'Disconnecting...' : 'Disconnect'}
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSaveWordpress} className="mt-2 space-y-4">
                <Input
                  label="WordPress site URL"
                  value={wordpressForm.baseUrl}
                  onChange={(event) => setWordpressForm((prev) => ({ ...prev, baseUrl: event.target.value }))}
                  placeholder="https://example.com"
                  hint="The plugin health endpoint is checked automatically before saving."
                  required
                />
                <Input
                  label="HMAC Secret"
                  type="password"
                  value={wordpressForm.hmacSecret}
                  onChange={(event) => setWordpressForm((prev) => ({ ...prev, hmacSecret: event.target.value }))}
                  placeholder="Shared secret from the plugin settings"
                  required
                />
                <p className="text-sm text-gray-500">
                  Upload the WordPress connector ZIP to the client site, activate it, and copy the shared secret from
                  the plugin settings page.
                </p>
                <Button type="submit" disabled={savingPlatform === 'wordpress'}>
                  {savingPlatform === 'wordpress' ? 'Connecting...' : 'Connect WordPress'}
                </Button>
              </form>
            )}
          </div>
        )}
      </Card>

      <div>
        <h2 className="mb-4 text-xl font-semibold text-gray-900">SEO tools</h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {tools.map((tool) => (
            <Link key={tool.name} to={tool.link}>
              <Card className="h-full p-6 transition-shadow hover:shadow-lg">
                <h3 className="text-lg font-semibold text-gray-900">{tool.name}</h3>
                <p className="mt-2 text-sm text-gray-600">{tool.description}</p>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

export default ProjectDetail
