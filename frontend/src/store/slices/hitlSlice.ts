import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { HITLTask, HITLApproval } from '@/types/hitl'
import { fetchHITLTasks, approveTask, rejectTask } from '@/api/hitlAPI'
import { getApiErrorMessage } from '@/api/authAPI'

interface HITLState {
  tasks: HITLTask[]
  loading: boolean
  error: string | null
}

const initialState: HITLState = {
  tasks: [],
  loading: false,
  error: null,
}

export const loadHITLTasks = createAsyncThunk(
  'hitl/loadTasks',
  async (_, { rejectWithValue }) => {
    try {
      return await fetchHITLTasks()
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to load HITL tasks.'))
    }
  }
)

export const approveHITLTask = createAsyncThunk(
  'hitl/approve',
  async (approval: HITLApproval, { rejectWithValue }) => {
    try {
      await approveTask(approval)
      return approval.taskId
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to approve HITL task.'))
    }
  }
)

export const rejectHITLTask = createAsyncThunk(
  'hitl/reject',
  async (approval: HITLApproval, { rejectWithValue }) => {
    try {
      await rejectTask(approval)
      return approval.taskId
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to reject HITL task.'))
    }
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
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to load tasks'
      })
      .addCase(approveHITLTask.fulfilled, (state, action) => {
        state.error = null
        state.tasks = state.tasks.filter(task => task.id !== action.payload && task.taskId !== action.payload)
      })
      .addCase(approveHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to approve task'
      })
      .addCase(rejectHITLTask.fulfilled, (state, action) => {
        state.error = null
        state.tasks = state.tasks.filter(task => task.id !== action.payload && task.taskId !== action.payload)
      })
      .addCase(rejectHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to reject task'
      })
  },
})

export default hitlSlice.reducer
