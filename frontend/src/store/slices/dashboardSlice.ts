import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import {
  AnalyzeBacklinkPayload,
  AnalyzeContentPayload,
  Backlink,
  ContentAnalysis,
  CreateProjectPayload,
  DashboardStats,
  OptimizedPage,
  Project,
} from '@/types/dashboard'
import {
  analyzeBacklink as analyzeBacklinkAPI,
  analyzeContent as analyzeContentAPI,
  createProject as createProjectAPI,
  deleteProject as deleteProjectAPI,
  fetchBacklinks as fetchBacklinksAPI,
  fetchDashboardStats as fetchDashboardStatsAPI,
  fetchOptimizedPages as fetchOptimizedPagesAPI,
  fetchProjectDetails as fetchProjectDetailsAPI,
  fetchProjects as fetchProjectsAPI,
} from '@/api/dashboardAPI'


interface DashboardState {
  projects: Project[]
  currentProject: Project | null
  stats: DashboardStats | null
  backlinks: Backlink[]
  contentAnalysis: ContentAnalysis | null
  optimizedPages: OptimizedPage[]
  loading: boolean
  error: string | null
}


const initialState: DashboardState = {
  projects: [],
  currentProject: null,
  stats: null,
  backlinks: [],
  contentAnalysis: null,
  optimizedPages: [],
  loading: false,
  error: null,
}


export const fetchProjects = createAsyncThunk('dashboard/fetchProjects', async () => {
  return await fetchProjectsAPI()
})


export const fetchDashboardStats = createAsyncThunk('dashboard/fetchDashboardStats', async () => {
  return await fetchDashboardStatsAPI()
})


export const fetchProjectDetails = createAsyncThunk(
  'dashboard/fetchProjectDetails',
  async (projectId: string | number) => {
    return await fetchProjectDetailsAPI(projectId)
  }
)


export const createProject = createAsyncThunk(
  'dashboard/createProject',
  async (payload: CreateProjectPayload) => {
    return await createProjectAPI(payload)
  }
)


export const deleteProject = createAsyncThunk(
  'dashboard/deleteProject',
  async (projectId: string | number) => {
    await deleteProjectAPI(projectId)
    return String(projectId)
  }
)


export const fetchBacklinks = createAsyncThunk(
  'dashboard/fetchBacklinks',
  async (projectId: string | number) => {
    return await fetchBacklinksAPI(projectId)
  }
)


export const analyzeBacklink = createAsyncThunk(
  'dashboard/analyzeBacklink',
  async (payload: AnalyzeBacklinkPayload) => {
    return await analyzeBacklinkAPI(payload)
  }
)


export const fetchOptimizedPages = createAsyncThunk(
  'dashboard/fetchOptimizedPages',
  async (projectId: string | number) => {
    return await fetchOptimizedPagesAPI(projectId)
  }
)


export const analyzeContent = createAsyncThunk(
  'dashboard/analyzeContent',
  async (payload: AnalyzeContentPayload) => {
    return await analyzeContentAPI(payload)
  }
)


const dashboardSlice = createSlice({
  name: 'dashboard',
  initialState,
  reducers: {
    clearDashboardError: (state) => {
      state.error = null
    },
    clearContentAnalysis: (state) => {
      state.contentAnalysis = null
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchProjects.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchProjects.fulfilled, (state, action) => {
        state.loading = false
        state.projects = action.payload
      })
      .addCase(fetchProjects.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load projects'
      })

      .addCase(fetchDashboardStats.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchDashboardStats.fulfilled, (state, action) => {
        state.loading = false
        state.stats = action.payload
      })
      .addCase(fetchDashboardStats.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load dashboard stats'
      })

      .addCase(fetchProjectDetails.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchProjectDetails.fulfilled, (state, action) => {
        state.loading = false
        state.currentProject = action.payload
      })
      .addCase(fetchProjectDetails.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load project details'
      })

      .addCase(createProject.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(createProject.fulfilled, (state, action) => {
        state.loading = false
        state.projects.unshift(action.payload)
      })
      .addCase(createProject.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to create project'
      })

      .addCase(deleteProject.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(deleteProject.fulfilled, (state, action) => {
        state.loading = false
        state.projects = state.projects.filter((project) => project.id !== action.payload)
      })
      .addCase(deleteProject.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to delete project'
      })

      .addCase(fetchBacklinks.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchBacklinks.fulfilled, (state, action) => {
        state.loading = false
        state.backlinks = action.payload
      })
      .addCase(fetchBacklinks.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load backlinks'
      })

      .addCase(analyzeBacklink.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(analyzeBacklink.fulfilled, (state, action) => {
        state.loading = false
        state.backlinks = action.payload
      })
      .addCase(analyzeBacklink.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to analyze backlink'
      })

      .addCase(fetchOptimizedPages.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchOptimizedPages.fulfilled, (state, action) => {
        state.loading = false
        state.optimizedPages = action.payload
      })
      .addCase(fetchOptimizedPages.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load optimized pages'
      })

      .addCase(analyzeContent.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(analyzeContent.fulfilled, (state, action) => {
        state.loading = false
        state.contentAnalysis = action.payload
      })
      .addCase(analyzeContent.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to analyze content'
      })
  },
})


export const { clearDashboardError, clearContentAnalysis } = dashboardSlice.actions
export default dashboardSlice.reducer
