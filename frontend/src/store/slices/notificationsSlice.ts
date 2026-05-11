import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'
import { fetchNotificationItems } from '@/api/notificationsAPI'
import type { NotificationItem } from '@/types/notifications'

const READ_STORAGE_KEY = 'seo_master_read_notifications'

interface NotificationsState {
  items: NotificationItem[]
  readIds: string[]
  loading: boolean
  error: string | null
  lastLoadedAt: string | null
}

const loadReadIds = (): string[] => {
  try {
    const rawValue = window.localStorage.getItem(READ_STORAGE_KEY)
    const parsed = rawValue ? JSON.parse(rawValue) : []
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

const saveReadIds = (ids: string[]) => {
  window.localStorage.setItem(READ_STORAGE_KEY, JSON.stringify(ids.slice(-200)))
}

const initialState: NotificationsState = {
  items: [],
  readIds: loadReadIds(),
  loading: false,
  error: null,
  lastLoadedAt: null,
}

export const loadNotifications = createAsyncThunk<NotificationItem[], void, { rejectValue: string }>(
  'notifications/load',
  async (_, { rejectWithValue }) => {
    try {
      return await fetchNotificationItems()
    } catch {
      return rejectWithValue('Не удалось загрузить уведомления.')
    }
  },
)

const markIdsRead = (state: NotificationsState, ids: string[]) => {
  const nextIds = Array.from(new Set([...state.readIds, ...ids]))
  state.readIds = nextIds
  saveReadIds(nextIds)
}

const notificationsSlice = createSlice({
  name: 'notifications',
  initialState,
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
      .addCase(loadNotifications.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(loadNotifications.fulfilled, (state, action) => {
        state.loading = false
        state.items = action.payload
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
