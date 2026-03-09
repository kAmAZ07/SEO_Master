import { configureStore } from '@reduxjs/toolkit'
import auditReducer from './slices/auditSlice'
import hitlReducer from './slices/hitlSlice'
import dashboardReducer from './slices/dashboardSlice'
import authReducer from './slices/authSlice'

export const store = configureStore({
  reducer: {
    audit: auditReducer,
    hitl: hitlReducer,
    dashboard: dashboardReducer,
    auth: authReducer,
  },
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
