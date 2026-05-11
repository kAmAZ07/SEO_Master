import { configureStore } from '@reduxjs/toolkit'
import auditReducer from './slices/auditSlice'
import hitlReducer from './slices/hitlSlice'
import dashboardReducer from './slices/dashboardSlice'
import authReducer from './slices/authSlice'
import notificationsReducer from './slices/notificationsSlice'

export const store = configureStore({
  reducer: {
    audit: auditReducer,
    hitl: hitlReducer,
    dashboard: dashboardReducer,
    auth: authReducer,
    notifications: notificationsReducer,
  },
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
