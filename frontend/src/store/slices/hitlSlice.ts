import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { HITLTask, HITLApproval } from '@/types/hitl'
import { fetchHITLTasks, approveTask, rejectTask } from '@/api/hitlAPI'

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
  async () => {
    const response = await fetchHITLTasks()
    return response
  }
)

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
        state.tasks = state.tasks.filter(task => task.id !== action.payload)
      })
      .addCase(rejectHITLTask.fulfilled, (state, action) => {
        state.tasks = state.tasks.filter(task => task.id !== action.payload)
      })
  },
})

export default hitlSlice.reducer
