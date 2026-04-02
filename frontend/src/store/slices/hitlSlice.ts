import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import { HITLApproval, HITLTask } from '@/types/hitl'
import { AddTrackedKeywordPayload, KeywordSearchResult, TrackedKeyword } from '@/types/keywords'
import { approveTask, fetchHITLTasks, rejectTask } from '@/api/hitlAPI'
import {
  addKeyword as addKeywordAPI,
  fetchTrackedKeywords as fetchTrackedKeywordsAPI,
  removeKeyword as removeKeywordAPI,
  searchKeywords as searchKeywordsAPI,
} from '@/api/keywordsAPI'


interface HITLState {
  tasks: HITLTask[]
  searchResults: KeywordSearchResult[]
  trackedKeywords: TrackedKeyword[]
  loading: boolean
  error: string | null
}


const initialState: HITLState = {
  tasks: [],
  searchResults: [],
  trackedKeywords: [],
  loading: false,
  error: null,
}


export const loadHITLTasks = createAsyncThunk('hitl/loadTasks', async () => {
  return await fetchHITLTasks()
})


export const approveHITLTask = createAsyncThunk(
  'hitl/approve',
  async (approval: HITLApproval) => {
    await approveTask(approval)
    return approval.taskId
  }
)


export const rejectHITLTask = createAsyncThunk(
  'hitl/reject',
  async (approval: HITLApproval) => {
    await rejectTask(approval)
    return approval.taskId
  }
)


export const searchKeywords = createAsyncThunk(
  'hitl/searchKeywords',
  async (payload: { keyword: string; projectId?: string | number }) => {
    return await searchKeywordsAPI(payload)
  }
)


export const fetchTrackedKeywords = createAsyncThunk(
  'hitl/fetchTrackedKeywords',
  async (projectId: string | number) => {
    return await fetchTrackedKeywordsAPI(projectId)
  }
)


export const addKeyword = createAsyncThunk(
  'hitl/addKeyword',
  async (payload: AddTrackedKeywordPayload) => {
    return await addKeywordAPI(payload)
  }
)


export const removeKeyword = createAsyncThunk(
  'hitl/removeKeyword',
  async (keywordId: string | number) => {
    await removeKeywordAPI(keywordId)
    return String(keywordId)
  }
)


const hitlSlice = createSlice({
  name: 'hitl',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(loadHITLTasks.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(loadHITLTasks.fulfilled, (state, action) => {
        state.loading = false
        state.tasks = action.payload
      })
      .addCase(loadHITLTasks.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load tasks'
      })

      .addCase(approveHITLTask.fulfilled, (state, action) => {
        state.tasks = state.tasks.filter((task) => task.id !== action.payload)
      })
      .addCase(rejectHITLTask.fulfilled, (state, action) => {
        state.tasks = state.tasks.filter((task) => task.id !== action.payload)
      })

      .addCase(searchKeywords.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(searchKeywords.fulfilled, (state, action) => {
        state.loading = false
        state.searchResults = action.payload
      })
      .addCase(searchKeywords.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to search keywords'
      })

      .addCase(fetchTrackedKeywords.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchTrackedKeywords.fulfilled, (state, action) => {
        state.loading = false
        state.trackedKeywords = action.payload
      })
      .addCase(fetchTrackedKeywords.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to load tracked keywords'
      })

      .addCase(addKeyword.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(addKeyword.fulfilled, (state, action) => {
        state.loading = false
        if (action.payload) {
          state.trackedKeywords.unshift(action.payload)
        }
      })
      .addCase(addKeyword.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to add keyword'
      })

      .addCase(removeKeyword.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(removeKeyword.fulfilled, (state, action) => {
        state.loading = false
        state.trackedKeywords = state.trackedKeywords.filter(
          (keyword) => String(keyword.id) !== action.payload
        )
      })
      .addCase(removeKeyword.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to remove keyword'
      })
  },
})


export default hitlSlice.reducer
