import { useCallback, useEffect, useRef, useState } from 'react'
import {
  checkVerification,
  fetchVerification,
  requestVerificationChallenge,
  revokeVerification,
  type VerificationChallenge,
} from '../../api/dashboardAPI'
import type { ProjectScope, VerificationState, VerificationSummary } from '../../types/dashboard'
import { parseApiError } from '../../utils/apiError'
import { describeProjectError } from '../../utils/projectErrors'
import { describeVerificationError, describeVerificationSummary, formatVerificationDate } from '../../utils/verification'
import Badge from '../ui/Badge'
import Button from '../ui/Button'
import Card from '../ui/Card'

interface ProjectOwnershipProps {
  projectId: string
  summary: VerificationSummary | null | undefined
  scope?: ProjectScope | null
  // Проект без корректной области (статус scope_review): подтверждать нечего.
  scopeReview: boolean
}

type Busy = 'challenge' | 'check' | 'revoke' | null
type Notice = { kind: 'success' | 'error'; text: string } | null

const BADGE_VARIANTS: Record<VerificationState, 'default' | 'success' | 'warning' | 'danger' | 'info'> = {
  unverified: 'default',
  pending: 'info',
  verified: 'success',
  stale: 'warning',
}

const ProjectOwnership = ({ projectId, summary: initialSummary, scope, scopeReview }: ProjectOwnershipProps) => {
  const [summary, setSummary] = useState<VerificationSummary>(initialSummary ?? { state: 'unverified' })
  const [challenge, setChallenge] = useState<VerificationChallenge | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [notice, setNotice] = useState<Notice>(null)
  const [cooldown, setCooldown] = useState(0)
  const [confirmRevoke, setConfirmRevoke] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const alive = useRef(true)
  const instructionsRef = useRef<HTMLHeadingElement | null>(null)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Фокус на инструкцию после выдачи файла: пользователь с клавиатуры сразу попадает к тексту.
  useEffect(() => {
    if (challenge) {
      instructionsRef.current?.focus()
    }
  }, [challenge])

  useEffect(() => {
    if (cooldown <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => setCooldown((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])

  const run = useCallback(
    async (action: Exclude<Busy, null>, task: () => Promise<void>) => {
      if (busy) {
        return
      }
      setBusy(action)
      setNotice(null)
      try {
        await task()
      } catch (error) {
        if (!alive.current) {
          return
        }
        const parsed = parseApiError(error)
        if (parsed.status === 429) {
          const seconds = parsed.retryAfter ?? 5
          setCooldown(seconds)
          setNotice({ kind: 'error', text: `Слишком частые запросы. Повторите через ${seconds} с.` })
        } else if (parsed.code === 'no_pending_challenge') {
          setNotice({ kind: 'error', text: 'Нет активного файла подтверждения. Сначала выдайте новый файл.' })
        } else if (parsed.code === 'nothing_to_revoke') {
          setNotice({ kind: 'error', text: 'Подтверждения нет, отзывать нечего.' })
        } else if (parsed.code === 'challenge_conflict') {
          setNotice({ kind: 'error', text: 'Запрос пересёкся с другим. Обновите страницу и повторите.' })
        } else {
          setNotice({ kind: 'error', text: describeProjectError(parsed, 'Не удалось выполнить действие. Повторите позже.') })
        }
      } finally {
        if (alive.current) {
          setBusy(null)
        }
      }
    },
    [busy],
  )

  const handleChallenge = () =>
    run('challenge', async () => {
      const result = await requestVerificationChallenge(projectId)
      if (!alive.current) {
        return
      }
      setChallenge(result)
      setSummary(result.verification)
      setNotice({ kind: 'success', text: 'Файл подтверждения подготовлен. Разместите его на сайте и нажмите «Проверить».' })
    })

  const handleCheck = () =>
    run('check', async () => {
      const result = await checkVerification(projectId)
      if (!alive.current) {
        return
      }
      setSummary(result.verification)
      if (result.verified) {
        setChallenge(null)
        setNotice({ kind: 'success', text: 'Владение сайтом подтверждено.' })
      } else {
        setNotice({ kind: 'error', text: describeVerificationError(result.errorCode) })
      }
    })

  const handleRevoke = () =>
    run('revoke', async () => {
      await revokeVerification(projectId)
      if (!alive.current) {
        return
      }
      setConfirmRevoke(false)
      setChallenge(null)
      try {
        setSummary(await fetchVerification(projectId))
      } catch {
        setSummary((current) => ({ ...current, state: 'unverified', verifiedAt: null, freshUntil: null }))
      }
      setNotice({ kind: 'success', text: 'Подтверждение отозвано. Расширенный аудит снова недоступен.' })
    })

  const copy = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(key)
    } catch {
      setCopied(null)
      setNotice({ kind: 'error', text: 'Не удалось скопировать: выделите текст и скопируйте вручную.' })
    }
  }

  const shownScope = summary.scope ?? scope
  const scopeText = shownScope?.origin
    ? `${shownScope.origin}${shownScope.pathPrefix && shownScope.pathPrefix !== '/' ? shownScope.pathPrefix : ''}`
    : null
  const canVerify = !scopeReview
  const checkDisabled = busy !== null || cooldown > 0 || summary.state !== 'pending'
  const attemptsLeft = summary.attempts
  const verifiedUntil = formatVerificationDate(summary.freshUntil)

  return (
    <Card className="p-6">
      <section aria-labelledby="ownership-title" aria-busy={busy !== null}>
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="ownership-title" className="text-xl font-semibold text-gray-900">Подтверждение владения сайтом</h2>
          <Badge variant={scopeReview ? 'warning' : BADGE_VARIANTS[summary.state] ?? 'default'}>
            {scopeReview ? 'Требует проверки области' : describeVerificationSummary(summary)}
          </Badge>
        </div>

        {scopeReview ? (
          <p className="mt-3 text-sm leading-6 text-gray-700">
            У проекта нет корректной области сайта, поэтому владение подтвердить нельзя. Проект доступен для просмотра, но
            расширенный аудит и зависящие от области действия отключены. Создайте проект с корректным адресом сайта.
          </p>
        ) : (
          <>
            <p className="mt-3 text-sm leading-6 text-gray-700">
              Расширенный аудит обходит сайт глубже публичного, поэтому требует подтверждения, что сайт принадлежит вам.
              {scopeText ? <> Область проверки: <span className="font-medium text-gray-900">{scopeText}</span>.</> : null}
            </p>
            {summary.state === 'verified' && verifiedUntil && (
              <p className="mt-2 text-sm text-gray-600">
                Подтверждение действует до {verifiedUntil}, затем его нужно будет пройти заново.
              </p>
            )}
            {summary.state === 'stale' && (
              <p className="mt-2 text-sm text-amber-800">
                Срок подтверждения истёк: расширенный аудит недоступен, пока вы не пройдёте проверку заново.
              </p>
            )}
            {summary.state === 'pending' && !challenge && (
              <p className="mt-2 text-sm text-gray-600">
                Файл подтверждения уже выдан, но токен показывается только один раз. Если файла у вас нет, выдайте новый.
              </p>
            )}
          </>
        )}

        <div role={notice?.kind === 'error' ? 'alert' : 'status'} className="mt-4">
          {notice && (
            <div
              className={
                notice.kind === 'error'
                  ? 'rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700'
                  : 'rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800'
              }
            >
              {notice.text}
            </div>
          )}
        </div>

        {challenge && (
          <div className="mt-4 space-y-3 rounded-lg border border-blue-200 bg-blue-50 p-4">
            <h3 ref={instructionsRef} tabIndex={-1} className="font-semibold text-blue-900 outline-none">
              Как подтвердить
            </h3>
            <ol className="list-decimal space-y-3 pl-5 text-sm text-blue-900">
              <li>
                Создайте текстовый файл, доступный по адресу:
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <code className="break-all rounded bg-white px-2 py-1">{challenge.fileUrl}</code>
                  <Button type="button" size="sm" variant="outline" onClick={() => void copy('url', challenge.fileUrl)} aria-label="Скопировать адрес файла">
                    {copied === 'url' ? 'Скопировано' : 'Копировать'}
                  </Button>
                </div>
              </li>
              <li>
                Содержимое файла — ровно одна строка (токен показывается один раз):
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <code className="break-all rounded bg-white px-2 py-1">{challenge.fileBody}</code>
                  <Button type="button" size="sm" variant="outline" onClick={() => void copy('body', challenge.fileBody)} aria-label="Скопировать содержимое файла">
                    {copied === 'body' ? 'Скопировано' : 'Копировать'}
                  </Button>
                </div>
              </li>
              <li>Файл должен отдаваться без редиректов и не как HTML. Затем нажмите «Проверить».</li>
            </ol>
            {challenge.expiresAt && (
              <p className="text-xs text-blue-800">Файл действителен до {new Date(challenge.expiresAt).toLocaleString('ru-RU')}.</p>
            )}
          </div>
        )}

        {summary.state === 'pending' && summary.lastErrorCode && !notice && (
          <p className="mt-3 text-sm text-gray-600">
            Последняя проверка: {describeVerificationError(summary.lastErrorCode)}
            {typeof attemptsLeft === 'number' ? ` Попыток: ${attemptsLeft}.` : ''}
          </p>
        )}

        {canVerify && (
          <div className="mt-4 flex flex-wrap gap-3">
            <Button type="button" onClick={() => void handleChallenge()} disabled={busy !== null}>
              {busy === 'challenge'
                ? 'Готовим файл...'
                : summary.state === 'verified'
                  ? 'Пройти подтверждение заново'
                  : 'Получить файл подтверждения'}
            </Button>
            <Button type="button" variant="outline" onClick={() => void handleCheck()} disabled={checkDisabled}>
              {busy === 'check' ? 'Проверяем...' : cooldown > 0 ? `Повторить через ${cooldown} с` : 'Проверить'}
            </Button>
            {summary.state === 'verified' &&
              (confirmRevoke ? (
                <>
                  <Button type="button" variant="destructive" onClick={() => void handleRevoke()} disabled={busy !== null}>
                    {busy === 'revoke' ? 'Отзываем...' : 'Да, отозвать'}
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => setConfirmRevoke(false)} disabled={busy !== null}>
                    Отмена
                  </Button>
                </>
              ) : (
                <Button type="button" variant="ghost" onClick={() => setConfirmRevoke(true)} disabled={busy !== null}>
                  Отозвать подтверждение
                </Button>
              ))}
          </div>
        )}
      </section>
    </Card>
  )
}

export default ProjectOwnership
