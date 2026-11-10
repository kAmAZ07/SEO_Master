import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AuditStatePanel from './AuditStatePanel'
import type { AuditStatus, AuditWorkflowStatus } from '../../types/audit'
import type { AuditLoadErrorKind } from '../../utils/auditStatus'

const audit = (status: AuditWorkflowStatus, over: Partial<AuditStatus> = {}): AuditStatus => ({
  uid: 'a1', id: 'a1', url: 'https://example.com', status, progress: 40, createdAt: '2026-11-10T00:00:00Z',
  score: null, issues: { passed: 0, warnings: 0, errors: 0 }, details: [], ...over,
})

const renderPanel = (
  value: AuditStatus | null,
  props: { error?: string | null; errorKind?: AuditLoadErrorKind | null; gaveUp?: boolean; onCancel?: () => void } = {},
) => {
  const onRetry = vi.fn()
  render(
    <AuditStatePanel
      audit={value}
      error={props.error ?? null}
      errorKind={props.errorKind ?? null}
      gaveUp={props.gaveUp ?? false}
      onRetry={onRetry}
      onCancel={props.onCancel}
    />,
  )
  return { onRetry }
}

describe('AuditStatePanel', () => {
  it('в очереди и выполняется: индикатор хода и подпись статуса', () => {
    renderPanel(audit('queued'))
    expect(screen.getByRole('heading', { name: 'Аудит в очереди' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Ход выполнения аудита' })).toHaveAttribute('aria-valuenow', '40')
  })

  it('отмена доступна только пока аудит активен и передана обработчику', async () => {
    const onCancel = vi.fn()
    renderPanel(audit('running'), { onCancel })
    await userEvent.click(screen.getByRole('button', { name: 'Отменить аудит' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('нет кнопки отмены без обработчика и на конечных статусах', () => {
    renderPanel(audit('cancelled'), { onCancel: vi.fn() })
    expect(screen.queryByRole('button', { name: 'Отменить аудит' })).not.toBeInTheDocument()
  })

  it('failed показывает ошибку и код', () => {
    renderPanel(audit('failed', { error: 'Сайт недоступен', errorCode: 'precheck_failed' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Аудит завершился с ошибкой')
    expect(screen.getByText('Сайт недоступен')).toBeInTheDocument()
    expect(screen.getByText('Код ошибки: precheck_failed')).toBeInTheDocument()
  })

  it('cancelled, expired и unknown — три разных состояния', () => {
    const cancelled = render(<AuditStatePanel audit={audit('cancelled')} error={null} errorKind={null} gaveUp={false} onRetry={vi.fn()} />)
    expect(screen.getByText('Аудит отменён')).toBeInTheDocument()
    cancelled.unmount()

    const expired = render(<AuditStatePanel audit={audit('expired')} error={null} errorKind={null} gaveUp={false} onRetry={vi.fn()} />)
    expect(screen.getByText('Срок хранения результата истёк')).toBeInTheDocument()
    expired.unmount()

    render(<AuditStatePanel audit={audit('unknown')} error={null} errorKind={null} gaveUp={false} onRetry={vi.fn()} />)
    expect(screen.getByText('Состояние аудита не удалось определить')).toBeInTheDocument()
  })

  it('partial показывает предупреждение и понятные причины', () => {
    renderPanel(audit('partial', { partialReasons: ['deadline', 'robots_blocked', 'new_unknown_code'] }))
    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('Аудит завершён частично')
    expect(banner).toHaveTextContent('исчерпано время проверки')
    expect(banner).toHaveTextContent('часть страниц закрыта правилами robots.txt')
    expect(banner).toHaveTextContent('new_unknown_code')
  })

  it('completed без данных — отдельное сообщение, а завершённый с данными — нет', () => {
    const { unmount } = render(<AuditStatePanel audit={audit('completed')} error={null} errorKind={null} gaveUp={false} onRetry={vi.fn()} />)
    expect(screen.getByText(/данных для отчёта нет/)).toBeInTheDocument()
    unmount()

    render(
      <AuditStatePanel
        audit={audit('completed', { pages: [{ url: 'https://example.com' }] })}
        error={null} errorKind={null} gaveUp={false} onRetry={vi.fn()}
      />,
    )
    expect(screen.queryByText(/данных для отчёта нет/)).not.toBeInTheDocument()
  })

  it('«не найден» и «истёк» — карточки без повтора', () => {
    const { unmount } = render(<AuditStatePanel audit={null} error="нет такого" errorKind="not_found" gaveUp onRetry={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Аудит не найден' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    unmount()

    render(<AuditStatePanel audit={null} error="истёк" errorKind="expired" gaveUp onRetry={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Срок хранения отчёта истёк' })).toBeInTheDocument()
  })

  it.each<AuditLoadErrorKind>(['network', 'timeout', 'unavailable'])(
    'временная ошибка %s: пока опрос идёт — «повторяем», после остановки — кнопка',
    async (kind) => {
      const first = render(<AuditStatePanel audit={audit('running')} error="нет связи" errorKind={kind} gaveUp={false} onRetry={vi.fn()} />)
      expect(screen.getByRole('alert')).toHaveTextContent('Повторяем попытку автоматически')
      expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
      first.unmount()

      const { onRetry } = renderPanel(audit('running'), { error: 'нет связи', errorKind: kind, gaveUp: true })
      await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))
      expect(onRetry).toHaveBeenCalledTimes(1)
    },
  )
})
