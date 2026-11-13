import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'
import { fetchNotificationItems } from '@/api/notificationsAPI'
import type { NotificationItem } from '@/types/notifications'
import type { RootState } from '../store'
import { loadUser, login, register } from './authSlice'

// Отметки «прочитано» хранятся отдельно для каждого аккаунта, чтобы они не переходили между пользователями.
const READ_STORAGE_PREFIX = 'seo_master_read_notifications:'

interface NotificationsState {
  // Аккаунт, которому принадлежат items/readIds. Ответы, пришедшие для другого аккаунта, отбрасываются.
  ownerId: string | null
  items: NotificationItem[]
  readIds: string[]
  loading: boolean
  error: string | null
  lastLoadedAt: string | null
}

const loadReadIds = (ownerId: string): string[] => {
  try {
    const rawValue = window.localStorage.getItem(`${READ_STORAGE_PREFIX}${ownerId}`)
    const parsed = rawValue ? JSON.parse(rawValue) : []
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

const saveReadIds = (ownerId: string, ids: string[]) => {
  try {
    window.localStorage.setItem(`${READ_STORAGE_PREFIX}${ownerId}`, JSON.stringify(ids.slice(-200)))
  } catch {
    // Хранилище недоступно: отметки живут до перезагрузки страницы.
  }
}

const createInitialState = (): NotificationsState => ({
  ownerId: null,
  items: [],
  readIds: [],
  loading: false,
  error: null,
  lastLoadedAt: null,
})

interface LoadedNotifications {
  ownerId: string | null
  items: NotificationItem[]
}

export const loadNotifications = createAsyncThunk<
  LoadedNotifications,
  void,
  { rejectValue: string; state: RootState }
>('notifications/load', async (_, { rejectWithValue, getState }) => {
  const ownerId = getState().auth.user?.id ?? null
  try {
    return { ownerId, items: await fetchNotificationItems() }
  } catch {
    return rejectWithValue('Не удалось загрузить уведомления.')
  }
})

const markIdsRead = (state: NotificationsState, ids: string[]) => {
  const nextIds = Array.from(new Set([...state.readIds, ...ids]))
  state.readIds = nextIds
  if (state.ownerId) {
    saveReadIds(state.ownerId, nextIds)
  }
}

const adoptOwner = (state: NotificationsState, ownerId: string) => {
  if (state.ownerId !== ownerId) {
    state.ownerId = ownerId
    state.items = []
    state.readIds = loadReadIds(ownerId)
  }
}

const notificationsSlice = createSlice({
  name: 'notifications',
  initialState: createInitialState,
  reducers: {
    markNotificationRead: (state, action: PayloadAction<string>) => {
      markIdsRead(state, [action.payload])
    },
    markAllNotificationsRead: (state) => {
      markIdsRead(state, state.items.map((item) => item.id))
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(login.fulfilled, (state, action) => adoptOwner(state, action.payload.user.id))
      .addCase(register.fulfilled, (state, action) => adoptOwner(state, action.payload.user.id))
      .addCase(loadUser.fulfilled, (state, action) => adoptOwner(state, action.payload.id))
      .addCase(loadNotifications.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(loadNotifications.fulfilled, (state, action) => {
        state.loading = false
        // Ответ предыдущего аккаунта или уже завершённой сессии не должен попасть в текущее состояние.
        if (action.payload.ownerId !== state.ownerId) {
          return
        }
        state.items = action.payload.items
        state.lastLoadedAt = new Date().toISOString()
      })
      .addCase(loadNotifications.rejected, (state, action) => {
        state.loading = false
        state.error = action.payload ?? action.error.message ?? 'Не удалось загрузить уведомления.'
      })
  },
})

export const { markAllNotificationsRead, markNotificationRead } = notificationsSlice.actions
export default notificationsSlice.reducer
