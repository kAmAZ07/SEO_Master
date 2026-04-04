import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { DashboardStats, Project } from '@/types/dashboard'
import {
  createProject as createProjectRequest,
  deleteProject as deleteProjectRequest,
  fetchDashboardStats,
  fetchProjectDetails,
  fetchProjects,
  type CreateProjectPayload,
} from '@/api/dashboardAPI'
import { getApiErrorMessage } from '@/api/authAPI'

interface DashboardState {
  projects: Project[]
  currentProject: Project | null
  stats: DashboardStats | null
  loading: boolean
  error: string | null
}

const initialState: DashboardState = {
  projects: [],
  currentProject: null,
  stats: null,
  loading: false,
  error: null,
}

export const loadProjects = createAsyncThunk<Project[], void, { rejectValue: string }>(
  'dashboard/loadProjects',
  async (_, { rejectWithValue }) => {
    try {
      return await fetchProjects()
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to load projects.'))
    }
  },
)

export const loadStats = createAsyncThunk<DashboardStats, void, { rejectValue: string }>(
  'dashboard/loadStats',
  async (_, { rejectWithValue }) => {
    try {
      return await fetchDashboardStats()
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to load dashboard statistics.'))
    }
  },
)

export const loadProjectDetails = createAsyncThunk<Project, string, { rejectValue: string }>(
  'dashboard/loadProjectDetails',
  async (projectId, { rejectWithValue }) => {
    try {
      return await fetchProjectDetails(projectId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to load project details.'))
    }
  },
)

export const createProject = createAsyncThunk<Project, CreateProjectPayload, { rejectValue: string }>(
  'dashboard/createProject',
  async (payload, { rejectWithValue }) => {
    try {
      return await createProjectRequest(payload)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to create the project.'))
    }
  },
)

export const removeProject = createAsyncThunk<string, string, { rejectValue: string }>(
  'dashboard/removeProject',
  async (projectId, { rejectWithValue }) => {
    try {
      await deleteProjectRequest(projectId)
      return projectId
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to delete the project.'))
    }
  },
)

const setLoading = (state: DashboardState) => {
  state.loading = true
  state.error = null
}

const setError = (state: DashboardState, message?: string | null) => {
  state.loading = false
  state.error = message || 'Unexpected dashboard error.'
}

const dashboardSlice = createSlice({
  name: 'dashboard',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(loadProjects.pending, setLoading)
      .addCase(loadProjects.fulfilled, (state, action) => {
        state.loading = false
        state.projects = action.payload
      })
      .addCase(loadProjects.rejected, (state, action) => setError(state, action.payload ?? action.error.message))
      .addCase(loadStats.pending, setLoading)
      .addCase(loadStats.fulfilled, (state, action) => {
        state.loading = false
        state.stats = action.payload
      })
      .addCase(loadStats.rejected, (state, action) => setError(state, action.payload ?? action.error.message))
      .addCase(loadProjectDetails.pending, setLoading)
      .addCase(loadProjectDetails.fulfilled, (state, action) => {
        state.loading = false
        state.currentProject = action.payload
      })
      .addCase(loadProjectDetails.rejected, (state, action) => setError(state, action.payload ?? action.error.message))
      .addCase(createProject.pending, setLoading)
      .addCase(createProject.fulfilled, (state, action) => {
        state.loading = false
        state.projects = [action.payload, ...state.projects]
      })
      .addCase(createProject.rejected, (state, action) => setError(state, action.payload ?? action.error.message))
      .addCase(removeProject.pending, setLoading)
      .addCase(removeProject.fulfilled, (state, action) => {
        state.loading = false
        state.projects = state.projects.filter((project) => project.id !== action.payload)
        if (state.currentProject?.id === action.payload) {
          state.currentProject = null
        }
      })
      .addCase(removeProject.rejected, (state, action) => setError(state, action.payload ?? action.error.message))
  },
})

export default dashboardSlice.reducer
