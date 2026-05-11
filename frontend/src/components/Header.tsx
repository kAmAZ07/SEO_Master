import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Bell, CheckCircle2, ClipboardCheck, FileCheck2, ListTodo, TrendingUp } from 'lucide-react'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { logout } from '../store/slices/authSlice'
import {
  loadNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../store/slices/notificationsSlice'
import type { NotificationCategory, NotificationItem } from '../types/notifications'

type NotificationFilter = 'all' | NotificationCategory

const filterLabels: Record<NotificationFilter, string> = {
  all: 'Все',
  action: 'Требуют действия',
  audit: 'Аудиты',
  task: 'Задачи',
  score: 'FF-Score',
  integration: 'Интеграции',
}

const formatTime = (value?: string | null) => {
  if (!value) return 'только что'
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

const severityClasses: Record<NotificationItem['severity'], string> = {
  info: 'bg-blue-50 text-blue-600',
  success: 'bg-emerald-50 text-emerald-600',
  warning: 'bg-amber-50 text-amber-600',
  critical: 'bg-red-50 text-red-600',
}

const NotificationIcon = ({ item }: { item: NotificationItem }) => {
  const className = 'h-4 w-4'
  if (item.category === 'action') return <ClipboardCheck className={className} />
  if (item.category === 'audit') return item.severity === 'critical' ? <AlertTriangle className={className} /> : <FileCheck2 className={className} />
  if (item.category === 'task') return item.severity === 'critical' ? <AlertTriangle className={className} /> : <ListTodo className={className} />
  if (item.category === 'score') return <TrendingUp className={className} />
  return item.severity === 'critical' ? <AlertTriangle className={className} /> : <CheckCircle2 className={className} />
}

const Header = () => {
  const dispatch = useAppDispatch()
  const { user } = useAppSelector((state) => state.auth)
  const { items, readIds, loading, error } = useAppSelector((state) => state.notifications)
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false)
  const [activeFilter, setActiveFilter] = useState<NotificationFilter>('all')
  const notificationsRef = useRef<HTMLDivElement | null>(null)

  const unreadCount = useMemo(
    () => items.filter((item) => !readIds.includes(item.id)).length,
    [items, readIds],
  )
  const actionCount = useMemo(
    () => items.filter((item) => item.requiresAction).length,
    [items],
  )
  const filteredItems = useMemo(
    () => activeFilter === 'all' ? items : items.filter((item) => item.category === activeFilter),
    [activeFilter, items],
  )
  const notificationLabel = unreadCount > 0
    ? `Уведомления: ${unreadCount} новых`
    : 'Нет новых уведомлений'

  useEffect(() => {
    void dispatch(loadNotifications())

    const timer = window.setInterval(() => {
      void dispatch(loadNotifications())
    }, 60000)

    return () => {
      window.clearInterval(timer)
    }
  }, [dispatch])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (notificationsRef.current && !notificationsRef.current.contains(event.target as Node)) {
        setIsNotificationsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleLogout = () => {
    dispatch(logout())
  }

  return (
    <header className="fixed top-0 left-0 right-0 z-50 h-16 border-b border-gray-200 bg-white">
      <div className="flex h-full items-center justify-between px-6">
        <Link to="/" className="flex items-center">
          <h1 className="text-2xl font-bold text-blue-600">SEO Master</h1>
        </Link>

        <div className="flex items-center gap-4">
          <div ref={notificationsRef} className="relative">
            <button
              type="button"
              aria-label={notificationLabel}
              title={notificationLabel}
              onClick={() => setIsNotificationsOpen((current) => !current)}
              className="relative flex h-10 w-10 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-700"
            >
              <Bell className="h-5 w-5" />
              {unreadCount > 0 && (
                <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-xs font-semibold leading-none text-white">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </button>

            {isNotificationsOpen && (
              <div className="absolute right-0 mt-3 w-[min(440px,calc(100vw-2rem))] overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl">
                <div className="border-b border-gray-200 px-4 py-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h2 className="text-sm font-semibold text-gray-900">Уведомления</h2>
                      <p className="mt-1 text-xs text-gray-500">
                        {actionCount > 0 ? `${actionCount} требуют действия` : 'Критичных действий сейчас нет'}
                      </p>
                    </div>
                    {items.length > 0 && (
                      <button
                        type="button"
                        onClick={() => dispatch(markAllNotificationsRead())}
                        className="text-xs font-medium text-blue-600 hover:text-blue-700"
                      >
                        Прочитать все
                      </button>
                    )}
                  </div>

                  <div className="mt-3 flex gap-1 overflow-x-auto">
                    {(['all', 'action', 'audit', 'task', 'score'] as NotificationFilter[]).map((filter) => (
                      <button
                        key={filter}
                        type="button"
                        onClick={() => setActiveFilter(filter)}
                        className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                          activeFilter === filter
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                        }`}
                      >
                        {filterLabels[filter]}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="max-h-[420px] overflow-y-auto">
                  {loading && !items.length ? (
                    <div className="px-4 py-8 text-center text-sm text-gray-500">Загружаем уведомления...</div>
                  ) : error && !items.length ? (
                    <div className="px-4 py-8 text-center text-sm text-red-600">{error}</div>
                  ) : filteredItems.length ? (
                    <div className="divide-y divide-gray-100">
                      {filteredItems.map((item) => {
                        const isUnread = !readIds.includes(item.id)
                        return (
                          <Link
                            key={item.id}
                            to={item.href}
                            onClick={() => {
                              dispatch(markNotificationRead(item.id))
                              setIsNotificationsOpen(false)
                            }}
                            className="flex gap-3 px-4 py-3 transition-colors hover:bg-gray-50"
                          >
                            <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${severityClasses[item.severity]}`}>
                              <NotificationIcon item={item} />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="flex items-start justify-between gap-3">
                                <span className="text-sm font-semibold text-gray-900">{item.title}</span>
                                <span className="shrink-0 text-xs text-gray-400">{formatTime(item.createdAt)}</span>
                              </span>
                              <span className="mt-1 line-clamp-2 block text-xs leading-5 text-gray-600">{item.description}</span>
                              <span className="mt-2 flex items-center gap-2 text-xs font-medium text-blue-600">
                                {item.actionLabel}
                                {isUnread && <span className="h-1.5 w-1.5 rounded-full bg-blue-600" />}
                              </span>
                            </span>
                          </Link>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="px-4 py-8 text-center text-sm text-gray-500">По этому фильтру уведомлений нет.</div>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3 border-l border-gray-200 pl-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 font-semibold text-blue-600">
              {user?.email ? user.email[0].toUpperCase() : 'U'}
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-medium text-gray-900">{user?.email || 'Пользователь'}</span>
              <button
                onClick={handleLogout}
                className="text-left text-xs text-gray-500 hover:text-gray-700"
              >
                Выйти
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}

export default Header
