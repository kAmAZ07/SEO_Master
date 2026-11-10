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
import { parseApiError } from '@/utils/apiError'

interface DashboardState {
  projects: Project[]
  currentProject: Project | null
  stats: DashboardStats | null
  loading: boolean
  error: string | null
  // Карточка проекта загружается отдельно от списков: её ошибки и ожидание не смешиваются с панелью.
  detailsLoading: boolean
  detailsError: string | null
  detailsNotFound: boolean
  // Запрос карточки, ответ которого ещё принимается; ответ по другому проекту/запросу отбрасывается.
  detailsRequestId: string | null
  projectsRequestId: string | null
  statsRequestId: string | null
}

interface DetailsRejection {
  message: string
  notFound: boolean
}

const initialState: DashboardState = {
  projects: [],
  currentProject: null,
  stats: null,
  loading: false,
  error: null,
  detailsLoading: false,
  detailsError: null,
  detailsNotFound: false,
  detailsRequestId: null,
  projectsRequestId: null,
  statsRequestId: null,
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

export const loadProjectDetails = createAsyncThunk<Project, string, { rejectValue: DetailsRejection }>(
  'dashboard/loadProjectDetails',
  async (projectId, { rejectWithValue }) => {
    try {
      return await fetchProjectDetails(projectId)
    } catch (error) {
      return rejectWithValue({
        message: getApiErrorMessage(error, 'Не удалось загрузить проект.'),
        notFound: parseApiError(error).status === 404,
      })
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
  initialState: () => initialState,
  reducers: {
    // Экран проекта закрыт или открыт другой: прежняя карточка не должна мелькать на новом маршруте.
    clearCurrentProject: (state) => {
      state.currentProject = null
      state.detailsLoading = false
      state.detailsError = null
      state.detailsNotFound = false
      state.detailsRequestId = null
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadProjects.pending, (state, action) => {
        setLoading(state)
        state.projectsRequestId = action.meta.requestId
      })
      .addCase(loadProjects.fulfilled, (state, action) => {
        if (state.projectsRequestId !== action.meta.requestId) {
          return
        }
        state.loading = false
        state.projects = action.payload
      })
      .addCase(loadProjects.rejected, (state, action) => {
        if (state.projectsRequestId !== action.meta.requestId) {
          return
        }
        setError(state, action.payload ?? action.error.message)
      })
      .addCase(loadStats.pending, (state, action) => {
        setLoading(state)
        state.statsRequestId = action.meta.requestId
      })
      .addCase(loadStats.fulfilled, (state, action) => {
        if (state.statsRequestId !== action.meta.requestId) {
          return
        }
        state.loading = false
        state.stats = action.payload
      })
      .addCase(loadStats.rejected, (state, action) => {
        if (state.statsRequestId !== action.meta.requestId) {
          return
        }
        setError(state, action.payload ?? action.error.message)
      })
      .addCase(loadProjectDetails.pending, (state, action) => {
        state.detailsRequestId = action.meta.requestId
        state.detailsLoading = true
        state.detailsError = null
        state.detailsNotFound = false
        if (state.currentProject?.id !== action.meta.arg) {
          state.currentProject = null
        }
      })
      .addCase(loadProjectDetails.fulfilled, (state, action) => {
        if (state.detailsRequestId !== action.meta.requestId) {
          return
        }
        state.detailsLoading = false
        state.currentProject = action.payload
      })
      .addCase(loadProjectDetails.rejected, (state, action) => {
        if (state.detailsRequestId !== action.meta.requestId) {
          return
        }
        state.detailsLoading = false
        state.detailsError = action.payload?.message ?? action.error.message ?? 'Не удалось загрузить проект.'
        state.detailsNotFound = Boolean(action.payload?.notFound)
        if (action.payload?.notFound) {
          state.currentProject = null
        }
      })
      .addCase(createProject.pending, setLoading)
      .addCase(createProject.fulfilled, (state, action) => {
        state.loading = false
        // Создание идемпотентно: для той же области сервер возвращает существующий проект, дубль в список не добавляем.
        state.projects = [action.payload, ...state.projects.filter((project) => project.id !== action.payload.id)]
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

export const { clearCurrentProject } = dashboardSlice.actions
export default dashboardSlice.reducer
