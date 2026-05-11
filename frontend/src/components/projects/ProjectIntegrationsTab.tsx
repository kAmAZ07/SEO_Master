import { useEffect, useMemo, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import {
  BarChart3,
  CheckCircle2,
  ExternalLink,
  Globe2,
  KeyRound,
  Layers3,
  Loader2,
  Search,
  ShieldCheck,
  Unplug,
} from 'lucide-react'
import {
  fetchProjectIntegrations,
  revokeIntegration,
  saveGA4Integration,
  saveGSCIntegration,
  saveTildaIntegration,
  saveWordpressIntegration,
  saveYandexIntegration,
} from '@/api/integrationsAPI'
import { getApiErrorMessage } from '@/api/authAPI'
import type { ProjectIntegrationStatus, SupportedIntegrationPlatform } from '@/types/integrations'
import Button from '@/components/ui/Button'
import Card from '@/components/ui/Card'
import Input from '@/components/ui/Input'
import { cn } from '@/utils/classNames'

interface ProjectIntegrationsTabProps {
  projectId: string
  projectUrl?: string | null
}

interface Notice {
  type: 'success' | 'error'
  text: string
}

const SUPPORTED_PLATFORMS: SupportedIntegrationPlatform[] = ['tilda', 'wordpress', 'gsc', 'ga4', 'yandex']

const platformMeta: Record<SupportedIntegrationPlatform, { label: string; description: string; accent: string; icon: typeof Layers3 }> = {
  tilda: {
    label: 'Tilda',
    description: 'Сохраните API-ключи Tilda для публикации страниц через SEO Master.',
    accent: 'emerald',
    icon: Layers3,
  },
  wordpress: {
    label: 'WordPress',
    description: 'Сохраните адрес сайта и HMAC-секрет для интеграции с плагином SEO Master.',
    accent: 'blue',
    icon: Globe2,
  },
  gsc: {
    label: 'Google Search Console',
    description: 'Подключите ресурс GSC для обогащения аудита данными о поисковом трафике.',
    accent: 'amber',
    icon: Search,
  },
  ga4: {
    label: 'Google Analytics 4',
    description: 'Сохраните идентификатор ресурса GA4 и учётные данные для отчётности.',
    accent: 'violet',
    icon: BarChart3,
  },
  yandex: {
    label: 'Yandex Webmaster',
    description: 'Подключите Яндекс.Вебмастер для мониторинга индексации и ошибок.',
    accent: 'rose',
    icon: Globe2,
  },
}

const textareaClassName =
  'w-full rounded-xl border border-gray-300 px-4 py-3 text-sm text-gray-900 shadow-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100'

const formatDate = (value?: string | null) => {
  if (!value) {
    return 'Не доступно'
  }

  const parsedDate = new Date(value)
  return Number.isNaN(parsedDate.getTime()) ? value : parsedDate.toLocaleString('ru-RU')
}

const IntegrationTextarea = ({
  label,
  value,
  onChange,
  placeholder,
  rows = 6,
  required = false,
}: {
  label: string
  value: string
  onChange: (event: ChangeEvent<HTMLTextAreaElement>) => void
  placeholder?: string
  rows?: number
  required?: boolean
}) => (
  <label className="block space-y-2">
    <span className="text-sm font-medium text-gray-900">{label}</span>
    <textarea
      className={textareaClassName}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      rows={rows}
      required={required}
      spellCheck={false}
    />
  </label>
)

const ProjectIntegrationsTab = ({ projectId, projectUrl }: ProjectIntegrationsTabProps) => {
  const [activePlatform, setActivePlatform] = useState<SupportedIntegrationPlatform>('tilda')
  const [integrations, setIntegrations] = useState<Partial<Record<SupportedIntegrationPlatform, ProjectIntegrationStatus>>>({})
  const [integrationsLoading, setIntegrationsLoading] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [savingPlatform, setSavingPlatform] = useState<SupportedIntegrationPlatform | null>(null)
  const [disconnectingPlatform, setDisconnectingPlatform] = useState<SupportedIntegrationPlatform | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const [tildaForm, setTildaForm] = useState({ publicKey: '', secretKey: '', projectId: '' })
  const [wordpressForm, setWordpressForm] = useState({ baseUrl: projectUrl ?? '', hmacSecret: '' })
  const [gscForm, setGscForm] = useState({ propertyUrl: projectUrl ?? '', credentialsJson: '', tokenJson: '' })
  const [ga4Form, setGa4Form] = useState({ propertyId: '', credentialsJson: '', tokenJson: '' })
  const [yandexForm, setYandexForm] = useState({ token: '', userId: '', hostId: '' })

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
          response.items.reduce<Partial<Record<SupportedIntegrationPlatform, ProjectIntegrationStatus>>>((acc, item) => {
            if (SUPPORTED_PLATFORMS.includes(item.platform as SupportedIntegrationPlatform)) {
              acc[item.platform as SupportedIntegrationPlatform] = item
            }
            return acc
          }, {}),
        )
      } catch (integrationError) {
        if (!cancelled) {
          setNotice({
            type: 'error',
            text: getApiErrorMessage(integrationError, 'Не удалось загрузить интеграции проекта.'),
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
    if (!projectUrl) {
      return
    }
    setWordpressForm((current) => (current.baseUrl ? current : { ...current, baseUrl: projectUrl }))
    setGscForm((current) => (current.propertyUrl ? current : { ...current, propertyUrl: projectUrl }))
  }, [projectUrl])

  const connectedCount = useMemo(
    () => SUPPORTED_PLATFORMS.filter((platform) => integrations[platform]?.connected).length,
    [integrations],
  )

  const activeIntegration = integrations[activePlatform]
  const isBusy = Boolean(savingPlatform || disconnectingPlatform)

  const handleRefresh = () => {
    setNotice(null)
    setReloadToken((value) => value + 1)
  }

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSavingPlatform(activePlatform)
    setNotice(null)

    try {
      let saved: ProjectIntegrationStatus

      if (activePlatform === 'tilda') {
        const publicKey = tildaForm.publicKey.trim()
        const secretKey = tildaForm.secretKey.trim()
        const externalProjectId = tildaForm.projectId.trim()
        if (!publicKey || !secretKey || !externalProjectId) {
          throw new Error('Для Tilda обязательны Public Key, Secret Key и Project ID.')
        }
        saved = await saveTildaIntegration(projectId, { publicKey, secretKey, projectId: externalProjectId })
        setTildaForm({ publicKey: '', secretKey: '', projectId: '' })
      } else if (activePlatform === 'wordpress') {
        const baseUrl = wordpressForm.baseUrl.trim()
        const hmacSecret = wordpressForm.hmacSecret.trim()
        if (!baseUrl || !hmacSecret) {
          throw new Error('Необходимо указать URL сайта WordPress и HMAC-секрет.')
        }
        saved = await saveWordpressIntegration(projectId, { baseUrl, hmacSecret })
        setWordpressForm((current) => ({ ...current, baseUrl: saved.siteUrl ?? baseUrl, hmacSecret: '' }))
      } else if (activePlatform === 'gsc') {
        const propertyUrl = gscForm.propertyUrl.trim()
        const credentialsJson = gscForm.credentialsJson.trim()
        const tokenJson = gscForm.tokenJson.trim()
        if (!propertyUrl || !credentialsJson) {
          throw new Error('Необходимо указать URL ресурса GSC и Credentials JSON.')
        }
        saved = await saveGSCIntegration(projectId, {
          propertyUrl,
          credentialsJson,
          tokenJson: tokenJson || undefined,
        })
        setGscForm((current) => ({ ...current, credentialsJson: '', tokenJson: '' }))
      } else if (activePlatform === 'ga4') {
        const propertyId = ga4Form.propertyId.trim()
        const credentialsJson = ga4Form.credentialsJson.trim()
        const tokenJson = ga4Form.tokenJson.trim()
        if (!propertyId || !credentialsJson) {
          throw new Error('Необходимо указать GA4 Property ID и Credentials JSON.')
        }
        saved = await saveGA4Integration(projectId, {
          propertyId,
          credentialsJson,
          tokenJson: tokenJson || undefined,
        })
        setGa4Form((current) => ({ ...current, credentialsJson: '', tokenJson: '' }))
      } else {
        const token = yandexForm.token.trim()
        const userId = yandexForm.userId.trim()
        const hostId = yandexForm.hostId.trim()
        if (!token || !userId || !hostId) {
          throw new Error('Необходимо указать токен, User ID и Host ID для Яндекс.')
        }
        saved = await saveYandexIntegration(projectId, { token, userId, hostId })
        setYandexForm({ token: '', userId: '', hostId: '' })
      }

      setIntegrations((current) => ({ ...current, [activePlatform]: saved }))
      setNotice({
        type: 'success',
        text: `${platformMeta[activePlatform as SupportedIntegrationPlatform].label} подключён и сохранён в зашифрованном хранилище проекта.`,
      })
    } catch (integrationError) {
      setNotice({
        type: 'error',
        text: getApiErrorMessage(integrationError, 'Не удалось сохранить учётные данные интеграции.'),
      })
    } finally {
      setSavingPlatform(null)
    }
  }

  const handleDisconnect = async (platform: SupportedIntegrationPlatform) => {
    const label = platformMeta[platform].label
    if (!window.confirm(`Отключить ${label} от этого проекта?`)) {
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
      setNotice({ type: 'success', text: `${label} отключён от проекта.` })
    } catch (integrationError) {
      setNotice({
        type: 'error',
        text: getApiErrorMessage(integrationError, 'Не удалось отключить интеграцию.'),
      })
    } finally {
      setDisconnectingPlatform(null)
    }
  }

  const renderConnectedState = () => {
    if (!activeIntegration?.connected) {
      return null
    }

    const rows: Array<{ label: string; value: string }> = []
    if (activeIntegration.siteUrl) rows.push({ label: 'Сайт / ресурс', value: activeIntegration.siteUrl })
    if (activeIntegration.projectIdentifier) rows.push({ label: 'Идентификатор', value: activeIntegration.projectIdentifier })
    if (activeIntegration.accountIdentifier) rows.push({ label: 'Аккаунт', value: activeIntegration.accountIdentifier })
    if (activeIntegration.authMode) rows.push({ label: 'Режим авторизации', value: activeIntegration.authMode })
    if (activeIntegration.hint) rows.push({ label: 'Подсказка', value: activeIntegration.hint })
    if (typeof activeIntegration.pageMappingsCount === 'number') {
      rows.push({ label: 'Сопоставлено страниц', value: String(activeIntegration.pageMappingsCount) })
    }
    rows.push({ label: 'Подключено', value: formatDate(activeIntegration.connectedAt) })

    return (
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/80 p-5">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-6 w-6 text-emerald-600" />
              <div>
                <h4 className="text-xl font-semibold text-emerald-950">{platformMeta[activePlatform as SupportedIntegrationPlatform].label} подключён</h4>
                <p className="text-sm text-emerald-800">Секреты хранятся в зашифрованном виде и не передаются в интерфейс.</p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 text-sm text-emerald-900 md:grid-cols-2">
              {rows.map((row) => (
                <p key={row.label}>
                  <span className="font-semibold">{row.label}:</span> {row.value}
                </p>
              ))}
            </div>
            {activePlatform === 'wordpress' && activeIntegration.pluginHealth?.healthUrl && (
              <a
                href={activeIntegration.pluginHealth.healthUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 text-sm font-medium text-emerald-700 hover:text-emerald-900"
              >
                Состояние плагина
                <ExternalLink className="h-4 w-4" />
              </a>
            )}
          </div>
          <Button
            type="button"
            variant="outline"
            className="border-emerald-300 bg-white text-emerald-800 hover:bg-emerald-100"
            onClick={() => void handleDisconnect(activePlatform)}
            disabled={disconnectingPlatform === activePlatform}
          >
            <Unplug className="mr-2 h-4 w-4" />
            {disconnectingPlatform === activePlatform ? 'Отключаем...' : 'Отключить'}
          </Button>
        </div>
      </div>
    )
  }

  const renderForm = () => {
    if (activeIntegration?.connected) {
      return renderConnectedState()
    }

    if (activePlatform === 'tilda') {
      return (
        <form onSubmit={handleSave} className="space-y-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Input label="Public Key" value={tildaForm.publicKey} onChange={(event) => setTildaForm((current) => ({ ...current, publicKey: event.target.value }))} placeholder="xxxxxx" autoComplete="off" required />
            <Input label="Secret Key" type="password" value={tildaForm.secretKey} onChange={(event) => setTildaForm((current) => ({ ...current, secretKey: event.target.value }))} placeholder="••••••••" autoComplete="new-password" required />
          </div>
          <Input label="Project ID" value={tildaForm.projectId} onChange={(event) => setTildaForm((current) => ({ ...current, projectId: event.target.value }))} placeholder="123456" autoComplete="off" required />
          <Button type="submit" disabled={savingPlatform === 'tilda'} className="w-full md:w-auto">
            <KeyRound className="mr-2 h-4 w-4" />
            {savingPlatform === 'tilda' ? 'Сохраняем...' : 'Подключить Tilda'}
          </Button>
        </form>
      )
    }

    if (activePlatform === 'wordpress') {
      return (
        <div className="space-y-6">
          <ol className="space-y-4">
            <li className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <p className="text-sm font-semibold text-gray-900">1. Скачайте плагин SEO Master</p>
              <p className="mt-1 text-sm text-gray-600">Скачайте ZIP-архив коннектора и сохраните на компьютер.</p>
              <a
                href="/api/wordpress-plugin"
                download
                className="mt-3 inline-flex items-center gap-2 rounded-lg border border-blue-300 bg-white px-4 py-2 text-sm font-medium text-blue-700 shadow-sm hover:bg-blue-50"
              >
                <ExternalLink className="h-4 w-4" />
                Download seo-master-connector.zip
              </a>
            </li>

            <li className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <p className="text-sm font-semibold text-gray-900">2. Установите и активируйте плагин</p>
              <p className="mt-1 text-sm text-gray-600">
                В панели WordPress перейдите в <strong>Плагины → Добавить новый → Загрузить плагин</strong>, выберите скачанный ZIP,
                нажмите «Установить» и затем «Активировать».
              </p>
            </li>

            <li className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <p className="text-sm font-semibold text-gray-900">3. Укажите HMAC-секрет в wp-config.php</p>
              <p className="mt-1 text-sm text-gray-600">
                Откройте <code className="rounded bg-gray-200 px-1 py-0.5 text-xs">wp-config.php</code> и добавьте строку ниже перед{' '}
                <code className="rounded bg-gray-200 px-1 py-0.5 text-xs">/* That&apos;s all */</code>. Используйте то же значение, что введёте на шаге 4.
              </p>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 px-4 py-3 text-xs text-emerald-300">
                {`define('SEO_MASTER_HMAC_SECRET', 'your-secret-here');`}
              </pre>
              <p className="mt-2 text-xs text-gray-500">
                Затем перейдите в <strong>Настройки → SEO Master Connector</strong> в панели WordPress и укажите <strong>Project ID</strong> текущего проекта.
              </p>
            </li>

            <li className="rounded-xl border border-blue-100 bg-blue-50 p-4">
              <p className="text-sm font-semibold text-gray-900">4. Подключите к SEO Master</p>
              <p className="mt-1 mb-4 text-sm text-gray-600">Введите URL сайта WordPress и тот же HMAC-секрет, что прописали в wp-config.php.</p>
              <form onSubmit={handleSave} className="space-y-4">
                <Input label="URL сайта WordPress" value={wordpressForm.baseUrl} onChange={(event) => setWordpressForm((current) => ({ ...current, baseUrl: event.target.value }))} placeholder="https://example.com" autoComplete="url" required />
                <Input label="HMAC Secret" type="password" value={wordpressForm.hmacSecret} onChange={(event) => setWordpressForm((current) => ({ ...current, hmacSecret: event.target.value }))} placeholder="your-secret-here" autoComplete="new-password" required />
                <Button type="submit" disabled={savingPlatform === 'wordpress'} className="w-full md:w-auto">
                  <KeyRound className="mr-2 h-4 w-4" />
                  {savingPlatform === 'wordpress' ? 'Сохраняем...' : 'Подключить WordPress'}
                </Button>
              </form>
            </li>
          </ol>
        </div>
      )
    }

    if (activePlatform === 'gsc') {
      return (
        <form onSubmit={handleSave} className="space-y-5">
          <Input label="URL ресурса" value={gscForm.propertyUrl} onChange={(event) => setGscForm((current) => ({ ...current, propertyUrl: event.target.value }))} placeholder="https://example.com/ или sc-domain:example.com" autoComplete="off" required />
          <IntegrationTextarea label="Credentials JSON" value={gscForm.credentialsJson} onChange={(event) => setGscForm((current) => ({ ...current, credentialsJson: event.target.value }))} placeholder='{"type":"service_account", ...}' required />
          <IntegrationTextarea label="Token JSON (необязательно)" value={gscForm.tokenJson} onChange={(event) => setGscForm((current) => ({ ...current, tokenJson: event.target.value }))} placeholder='{"token":"...","refresh_token":"..."}' rows={4} />
          <Button type="submit" disabled={savingPlatform === 'gsc'} className="w-full md:w-auto">
            <KeyRound className="mr-2 h-4 w-4" />
            {savingPlatform === 'gsc' ? 'Сохраняем...' : 'Подключить Search Console'}
          </Button>
        </form>
      )
    }

    if (activePlatform === 'ga4') {
      return (
        <form onSubmit={handleSave} className="space-y-5">
          <Input label="GA4 Property ID" value={ga4Form.propertyId} onChange={(event) => setGa4Form((current) => ({ ...current, propertyId: event.target.value }))} placeholder="123456789 or properties/123456789" autoComplete="off" required />
          <IntegrationTextarea label="Credentials JSON" value={ga4Form.credentialsJson} onChange={(event) => setGa4Form((current) => ({ ...current, credentialsJson: event.target.value }))} placeholder='{"type":"service_account", ...}' required />
          <IntegrationTextarea label="Token JSON (необязательно)" value={ga4Form.tokenJson} onChange={(event) => setGa4Form((current) => ({ ...current, tokenJson: event.target.value }))} placeholder='{"token":"...","refresh_token":"..."}' rows={4} />
          <Button type="submit" disabled={savingPlatform === 'ga4'} className="w-full md:w-auto">
            <KeyRound className="mr-2 h-4 w-4" />
            {savingPlatform === 'ga4' ? 'Сохраняем...' : 'Подключить GA4'}
          </Button>
        </form>
      )
    }

    return (
      <form onSubmit={handleSave} className="space-y-5">
        <Input label="OAuth Token" type="password" value={yandexForm.token} onChange={(event) => setYandexForm((current) => ({ ...current, token: event.target.value }))} placeholder="Yandex OAuth token" autoComplete="new-password" required />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Input label="User ID" value={yandexForm.userId} onChange={(event) => setYandexForm((current) => ({ ...current, userId: event.target.value }))} placeholder="12345678" autoComplete="off" required />
          <Input label="Host ID" value={yandexForm.hostId} onChange={(event) => setYandexForm((current) => ({ ...current, hostId: event.target.value }))} placeholder="https:example.com:443" autoComplete="off" required />
        </div>
        <Button type="submit" disabled={savingPlatform === 'yandex'} className="w-full md:w-auto">
          <KeyRound className="mr-2 h-4 w-4" />
          {savingPlatform === 'yandex' ? 'Сохраняем...' : 'Подключить Yandex Webmaster'}
        </Button>
      </form>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden border-0 bg-slate-950 text-white shadow-xl shadow-slate-900/10">
        <div className="relative p-6 md:p-8">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.34),transparent_34%),radial-gradient(circle_at_bottom_left,rgba(16,185,129,0.24),transparent_32%)]" />
          <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl">
              <p className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-sm text-slate-100">
                <ShieldCheck className="h-4 w-4" />
                Хранилище учётных данных
              </p>
              <h2 className="mt-5 text-3xl font-bold tracking-tight">Интеграции проекта</h2>
              <p className="mt-3 text-sm leading-6 text-slate-200">
                Подключайте CMS, аналитику и инструменты вебмастера на уровне проекта. Чувствительные данные шифруются на сервере
                с помощью <code>MASTER_ENCRYPTION_KEY</code> и в интерфейс не возвращаются.
              </p>
            </div>
            <div className="grid min-w-[220px] grid-cols-2 gap-3 rounded-2xl border border-white/15 bg-white/10 p-4 backdrop-blur">
              <div>
                <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Подключено</p>
                <p className="mt-2 text-3xl font-bold">{connectedCount}/{SUPPORTED_PLATFORMS.length}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.22em] text-slate-300">Проект</p>
                <p className="mt-2 truncate text-sm font-semibold text-white">{projectId}</p>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {SUPPORTED_PLATFORMS.map((platform) => {
          const meta = platformMeta[platform]
          const Icon = meta.icon
          const integration = integrations[platform]
          return (
            <button
              key={platform}
              type="button"
              onClick={() => setActivePlatform(platform)}
              aria-pressed={activePlatform === platform}
              className={cn(
                'rounded-2xl border bg-white p-5 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md',
                activePlatform === platform ? 'border-blue-400 ring-4 ring-blue-100' : 'border-gray-200',
              )}
            >
              <div className="flex items-start gap-4">
                <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
                  <Icon className="h-6 w-6" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-lg font-semibold text-gray-900">{meta.label}</h3>
                    <span className={cn('rounded-full px-3 py-1 text-xs font-semibold', integration?.connected ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-600')}>
                      {integration?.connected ? 'Подключено' : 'Не настроено'}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-gray-600">{meta.description}</p>
                </div>
              </div>
            </button>
          )
        })}
      </div>

      <Card className="overflow-hidden">
        <div className="border-b border-gray-200 bg-gray-50 px-5 py-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.2em] text-gray-400">Настройка подключения</p>
              <h3 className="mt-1 text-2xl font-semibold text-gray-900">{platformMeta[activePlatform as SupportedIntegrationPlatform].label}</h3>
            </div>
            <Button type="button" variant="outline" onClick={handleRefresh} disabled={integrationsLoading || isBusy}>
              {integrationsLoading ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Обновляем
                </span>
              ) : (
                'Обновить статус'
              )}
            </Button>
          </div>
        </div>

        {notice && (
          <div className={cn('mx-5 mt-5 rounded-xl border px-4 py-3 text-sm', notice.type === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-700')}>
            {notice.text}
          </div>
        )}

        <div className="p-5 md:p-6">{renderForm()}</div>
      </Card>
    </div>
  )
}

export default ProjectIntegrationsTab




