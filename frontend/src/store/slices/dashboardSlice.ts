import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { Project, DashboardStats } from '@/types/dashboard'
import { fetchProjects, fetchDashboardStats } from '@/api/dashboardAPI'

interface DashboardState {
  projects: Project[]
  stats: DashboardStats | null
  loading: boolean
  error: string | null
}

const initialState: DashboardState = {
  projects: [],
  stats: null,
  loading: false,
  error: null,
}

export const loadProjects = createAsyncThunk(
  'dashboard/loadProjects',
  async () => {
    const response = await fetchProjects()
    return response
  }
)

export const loadStats = createAsyncThunk(
  'dashboard/loadStats',
  async () => {
    const response = await fetchDashboardStats()
    return response
  }
)

const dashboardSlice = createSlice({
  name: 'dashboard',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(loadProjects.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(loadProjects.fulfilled, (state, action) => {
        state.loading = false
        state.projects = action.payload
      })
      .addCase(loadProjects.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load projects'
      })
      .addCase(loadStats.fulfilled, (state, action) => {
        state.stats = action.payload
      })
  },
})

export default dashboardSlice.reducer
