import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2, ExternalLink, Globe2, KeyRound, Layers3, Loader2, ShieldCheck, Unplug } from 'lucide-react'
import {
  fetchProjectIntegrations,
  revokeIntegration,
  saveTildaIntegration,
  saveWordpressIntegration,
} from '@/api/integrationsAPI'
import { getApiErrorMessage } from '@/api/authAPI'
import type { ProjectIntegrationStatus } from '@/types/integrations'
import Button from '@/components/ui/Button'
import Card from '@/components/ui/Card'
import Input from '@/components/ui/Input'
import { cn } from '@/utils/classNames'

type IntegrationPlatform = 'tilda' | 'wordpress'

interface ProjectIntegrationsTabProps {
  projectId: string
  projectUrl?: string | null
}

interface Notice {
  type: 'success' | 'error'
  text: string
}

const platformLabels: Record<IntegrationPlatform, string> = {
  tilda: 'Tilda',
  wordpress: 'WordPress',
}

const formatDate = (value?: string | null) => {
  if (!value) {
    return 'Недоступно'
  }

  const parsedDate = new Date(value)
  return Number.isNaN(parsedDate.getTime()) ? value : parsedDate.toLocaleString('ru-RU')
}

const ProjectIntegrationsTab = ({ projectId, projectUrl }: ProjectIntegrationsTabProps) => {
  const [activePlatform, setActivePlatform] = useState<IntegrationPlatform>('tilda')
  const [integrations, setIntegrations] = useState<Partial<Record<IntegrationPlatform, ProjectIntegrationStatus>>>({})
  const [integrationsLoading, setIntegrationsLoading] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [savingPlatform, setSavingPlatform] = useState<IntegrationPlatform | null>(null)
  const [disconnectingPlatform, setDisconnectingPlatform] = useState<IntegrationPlatform | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [tildaForm, setTildaForm] = useState({
    publicKey: '',
    secretKey: '',
    projectId: '',
  })
  const [wordpressForm, setWordpressForm] = useState({
    baseUrl: projectUrl ?? '',
    hmacSecret: '',
  })

  useEffect(() => {
    let cancelled = false

    const loadIntegrations = async () => {
      setIntegrationsLoading(true)

      try {
        const response = await fetchProjectIntegrations(projectId)
        if (cancelled) {
          return
        }

        setIntegrations(
          response.items.reduce<Partial<Record<IntegrationPlatform, ProjectIntegrationStatus>>>((acc, item) => {
            if (item.platform === 'tilda' || item.platform === 'wordpress') {
              acc[item.platform] = item
            }
            return acc
          }, {}),
        )
      } catch (integrationError) {
        if (!cancelled) {
          setNotice({
            type: 'error',
            text: getApiErrorMessage(integrationError, 'Не удалось загрузить настройки интеграций.'),
          })
        }
      } finally {
        if (!cancelled) {
          setIntegrationsLoading(false)
        }
      }
    }

    void loadIntegrations()

    return () => {
      cancelled = true
    }
  }, [projectId, reloadToken])

  useEffect(() => {
    setWordpressForm((current) => {
      if (current.baseUrl || !projectUrl) {
        return current
      }
      return { ...current, baseUrl: projectUrl }
    })
  }, [projectUrl])

  const tildaIntegration = integrations.tilda
  const wordpressIntegration = integrations.wordpress
  const connectedCount = Object.values(integrations).filter((integration) => integration?.connected).length
  const isBusy = Boolean(savingPlatform || disconnectingPlatform)

  const handleRefresh = () => {
    setNotice(null)
    setReloadToken((value) => value + 1)
  }

  const handleSaveTilda = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    const publicKey = tildaForm.publicKey.trim()
    const secretKey = tildaForm.secretKey.trim()
    const externalProjectId = tildaForm.projectId.trim()

    if (!publicKey || !secretKey || !externalProjectId) {
      setNotice({ type: 'error', text: 'Заполните Public Key, Secret Key и Project ID для Tilda.' })
      return
    }

    setSavingPlatform('tilda')
    setNotice(null)

    try {
      const saved = await saveTildaIntegration(projectId, {
        publicKey,
        secretKey,
        projectId: externalProjectId,
      })
      setIntegrations((current) => ({ ...current, tilda: saved }))
      setTildaForm({ publicKey: '', secretKey: '', projectId: '' })
      setNotice({
        type: 'success',
        text: 'Tilda подключена. Ключи сохранены только в зашифрованном виде, в интерфейсе оставлен masked hint.',
      })
    } catch (integrationError) {
      setNotice({
        type: 'error',
        text: getApiErrorMessage(integrationError, 'Не удалось подключить Tilda.'),
      })
    } finally {
      setSavingPlatform(null)
    }
  }

  const handleSaveWordpress = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    const baseUrl = wordpressForm.baseUrl.trim()
    const hmacSecret = wordpressForm.hmacSecret.trim()

    if (!baseUrl || !hmacSecret) {
      setNotice({ type: 'error', text: 'Заполните URL сайта WordPress и HMAC Secret.' })
      return
    }

    setSavingPlatform('wordpress')
    setNotice(null)

    try {
      const saved = await saveWordpressIntegration(projectId, {
        baseUrl,
        hmacSecret,
      })
      setIntegrations((current) => ({ ...current, wordpress: saved }))
      setWordpressForm({ baseUrl: saved.siteUrl ?? baseUrl, hmacSecret: '' })
      setNotice({
        type: 'success',
        text: 'WordPress подключен. Секрет не возвращается из API и очищен из формы после сохранения.',
      })
    } catch (integrationError) {
      setNotice({
        type: 'error',
        text: getApiErrorMessage(integrationError, 'Не удалось подключить WordPress.'),
      })
    } finally {
      setSavingPlatform(null)
    }
  }

  const handleDisconnect = async (platform: IntegrationPlatform) => {
    if (!window.confirm(`Отключить ${platformLabels[platform]} для этого проекта?`)) {
      return
    }

    setDisconnectingPlatform(platform)
    setNotice(null)

    try {
      await revokeIntegration(projectId, platform)
      setIntegrations((current) => ({
        ...current,
        [platform]: {
          platform,
          connected: false,
          status: 'not_configured',
        },
      }))
      setNotice({ type: 'success', text: `${platformLabels[platform]} отключен для этого проекта.` })
    } catch (integrationError) {
      setNotice({
        type: 'error',
        text: getApiErrorMessage(integrationError, 'Не удалось отключить интеграцию.'),
      })
    } finally {
      setDisconnectingPlatform(null)
    }
  }

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden border-0 bg-slate-950 text-white shadow-xl shadow-slate-900/10">
        <div className="relative p-6 md:p-8">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.34),transparent_34%),radial-gradient(circle_at_bottom_left,rgba(16,185,129,0.24),transparent_32%)]" />
          <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <p className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-sm text-slate-100">
                <ShieldCheck className="h-4 w-4" />
                Per-project credentials vault
              </p>
              <h2 className="mt-5 text-3xl font-bold tracking-tight">Интеграции проекта</h2>
              <p className="mt-3 text-sm leading-6 text-slate-200">
                Подключите Tilda или WordPress к конкретному проекту. Ключи отправляются в backend, шифруются через
                MASTER_ENCRYPTION_KEY и дальше отображаются только как короткий hint.
              </p>
            </div>
            <div className="grid min-w-[220px] grid-cols-2 gap-3 rounded-2xl border border-white/15 bg-white/10 p-4 backdrop-blur">
              <div>
                <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Активно</p>
                <p className="mt-2 text-3xl font-bold">{connectedCount}/2</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Проект</p>
                <p className="mt-2 truncate text-sm font-semibold text-white">{projectId}</p>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <button
          type="button"
          onClick={() => setActivePlatform('tilda')}
          aria-pressed={activePlatform === 'tilda'}
          className={cn(
            'rounded-2xl border bg-white p-5 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md',
            activePlatform === 'tilda' ? 'border-emerald-400 ring-4 ring-emerald-100' : 'border-gray-200',
          )}
        >
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-emerald-100 p-3 text-emerald-700">
              <Layers3 className="h-6 w-6" />
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-lg font-semibold text-gray-900">Tilda</h3>
                <span
                  className={cn(
                    'rounded-full px-3 py-1 text-xs font-semibold',
                    tildaIntegration?.connected ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-600',
                  )}
                >
                  {tildaIntegration?.connected ? 'Подключена' : 'Не подключена'}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-gray-600">
                API-ключи и Project ID хранятся отдельно для этого проекта. Используется для деплоя Tilda change-set.
              </p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => setActivePlatform('wordpress')}
          aria-pressed={activePlatform === 'wordpress'}
          className={cn(
            'rounded-2xl border bg-white p-5 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md',
            activePlatform === 'wordpress' ? 'border-blue-400 ring-4 ring-blue-100' : 'border-gray-200',
          )}
        >
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-blue-100 p-3 text-blue-700">
              <Globe2 className="h-6 w-6" />
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-lg font-semibold text-gray-900">WordPress</h3>
                <span
                  className={cn(
                    'rounded-full px-3 py-1 text-xs font-semibold',
                    wordpressIntegration?.connected ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600',
                  )}
                >
                  {wordpressIntegration?.connected ? 'Подключена' : 'Не подключена'}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-gray-600">
                URL сайта и HMAC Secret проверяются через plugin health endpoint перед сохранением credentials.
              </p>
            </div>
          </div>
        </button>
      </div>

      <Card className="overflow-hidden">
        <div className="border-b border-gray-200 bg-gray-50 px-5 py-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.2em] text-gray-400">Подключить сайт</p>
              <h3 className="mt-1 text-2xl font-semibold text-gray-900">{platformLabels[activePlatform]}</h3>
            </div>
            <Button type="button" variant="outline" onClick={handleRefresh} disabled={integrationsLoading || isBusy}>
              {integrationsLoading ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Обновляю
                </span>
              ) : (
                'Обновить статус'
              )}
            </Button>
          </div>
        </div>

        {notice && (
          <div
            className={cn(
              'mx-5 mt-5 rounded-xl border px-4 py-3 text-sm',
              notice.type === 'success'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                : 'border-red-200 bg-red-50 text-red-700',
            )}
          >
            {notice.text}
          </div>
        )}

        {activePlatform === 'tilda' && (
          <div className="p-5 md:p-6">
            {tildaIntegration?.connected ? (
              <div className="rounded-2xl border border-emerald-200 bg-emerald-50/80 p-5">
                <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                      <div>
                        <h4 className="text-xl font-semibold text-emerald-950">Tilda подключена</h4>
                        <p className="text-sm text-emerald-800">Секреты скрыты и не возвращаются в UI.</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-3 text-sm text-emerald-900 md:grid-cols-2">
                      <p>
                        <span className="font-semibold">Public Key:</span> {tildaIntegration.hint || 'hidden'}
                      </p>
                      <p>
                        <span className="font-semibold">Project ID:</span>{' '}
                        {tildaIntegration.projectIdentifier || 'не указан'}
                      </p>
                      <p>
                        <span className="font-semibold">Подключено:</span> {formatDate(tildaIntegration.connectedAt)}
                      </p>
                      <p>
                        <span className="font-semibold">Page mappings:</span>{' '}
                        {tildaIntegration.pageMappingsCount ?? 0}
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="border-emerald-300 bg-white text-emerald-800 hover:bg-emerald-100"
                    onClick={() => void handleDisconnect('tilda')}
                    disabled={disconnectingPlatform === 'tilda'}
                  >
                    <Unplug className="mr-2 h-4 w-4" />
                    {disconnectingPlatform === 'tilda' ? 'Отключаю...' : 'Отключить'}
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSaveTilda} className="space-y-5">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Input
                    label="Public Key"
                    value={tildaForm.publicKey}
                    onChange={(event) => setTildaForm((current) => ({ ...current, publicKey: event.target.value }))}
                    placeholder="xxxxxxxx..."
                    autoComplete="off"
                    required
                  />
                  <Input
                    label="Secret Key"
                    type="password"
                    value={tildaForm.secretKey}
                    onChange={(event) => setTildaForm((current) => ({ ...current, secretKey: event.target.value }))}
                    placeholder="••••••••"
                    autoComplete="new-password"
                    required
                  />
                </div>
                <Input
                  label="Project ID"
                  value={tildaForm.projectId}
                  onChange={(event) => setTildaForm((current) => ({ ...current, projectId: event.target.value }))}
                  placeholder="123456"
                  autoComplete="off"
                  required
                />
                <div className="rounded-2xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-900">
                  <p className="font-semibold">Где взять ключи:</p>
                  <p>Tilda - Настройки сайта - Экспорт - API Integration. ZIP-адаптер должен быть установлен на стороне сайта.</p>
                </div>
                <Button type="submit" disabled={savingPlatform === 'tilda'} className="w-full md:w-auto">
                  <KeyRound className="mr-2 h-4 w-4" />
                  {savingPlatform === 'tilda' ? 'Подключаю...' : 'Подключить Tilda'}
                </Button>
              </form>
            )}
          </div>
        )}

        {activePlatform === 'wordpress' && (
          <div className="p-5 md:p-6">
            {wordpressIntegration?.connected ? (
              <div className="rounded-2xl border border-blue-200 bg-blue-50/80 p-5">
                <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <CheckCircle2 className="h-6 w-6 text-blue-600" />
                      <div>
                        <h4 className="text-xl font-semibold text-blue-950">WordPress подключен</h4>
                        <p className="text-sm text-blue-800">HMAC Secret скрыт и используется только на время запроса.</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-3 text-sm text-blue-900 md:grid-cols-2">
                      <p>
                        <span className="font-semibold">Site URL:</span> {wordpressIntegration.siteUrl || 'не указан'}
                      </p>
                      <p>
                        <span className="font-semibold">Secret:</span> {wordpressIntegration.hint || 'hidden'}
                      </p>
                      <p>
                        <span className="font-semibold">Plugin:</span>{' '}
                        {wordpressIntegration.pluginHealth?.status || 'ok'}
                        {wordpressIntegration.pluginHealth?.version
                          ? `, v${wordpressIntegration.pluginHealth.version}`
                          : ''}
                      </p>
                      <p>
                        <span className="font-semibold">Подключено:</span>{' '}
                        {formatDate(wordpressIntegration.connectedAt)}
                      </p>
                    </div>
                    {wordpressIntegration.pluginHealth?.healthUrl && (
                      <a
                        href={wordpressIntegration.pluginHealth.healthUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-2 text-sm font-medium text-blue-700 hover:text-blue-900"
                      >
                        Plugin health endpoint
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="border-blue-300 bg-white text-blue-800 hover:bg-blue-100"
                    onClick={() => void handleDisconnect('wordpress')}
                    disabled={disconnectingPlatform === 'wordpress'}
                  >
                    <Unplug className="mr-2 h-4 w-4" />
                    {disconnectingPlatform === 'wordpress' ? 'Отключаю...' : 'Отключить'}
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSaveWordpress} className="space-y-5">
                <Input
                  label="WordPress site URL"
                  value={wordpressForm.baseUrl}
                  onChange={(event) => setWordpressForm((current) => ({ ...current, baseUrl: event.target.value }))}
                  placeholder="https://example.com"
                  autoComplete="url"
                  required
                />
                <Input
                  label="HMAC Secret"
                  type="password"
                  value={wordpressForm.hmacSecret}
                  onChange={(event) => setWordpressForm((current) => ({ ...current, hmacSecret: event.target.value }))}
                  placeholder="Shared secret from the plugin settings"
                  autoComplete="new-password"
                  required
                />
                <div className="rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm leading-6 text-blue-900">
                  <p className="font-semibold">Перед подключением:</p>
                  <p>
                    Установите WordPress ZIP-адаптер, активируйте плагин и скопируйте HMAC Secret. Backend
                    автоматически проверит доступность plugin health endpoint.
                  </p>
                </div>
                <Button type="submit" disabled={savingPlatform === 'wordpress'} className="w-full md:w-auto">
                  <KeyRound className="mr-2 h-4 w-4" />
                  {savingPlatform === 'wordpress' ? 'Проверяю и подключаю...' : 'Подключить WordPress'}
                </Button>
              </form>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

export default ProjectIntegrationsTab
