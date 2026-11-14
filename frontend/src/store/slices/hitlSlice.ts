import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { HITLTask, HITLApproval } from '@/types/hitl'
import { fetchHITLTasks, fetchTaskDetails, approveTask, rejectTask, applyTask, cancelOperation } from '@/api/hitlAPI'
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
      return await fetchTaskDetails(approval.taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to approve HITL task.'))
    }
  }
)

export const applyHITLTask = createAsyncThunk(
  'hitl/apply',
  async ({ approval, operationId }: { approval: HITLApproval; operationId: string }, { rejectWithValue }) => {
    try {
      await applyTask(approval, operationId)
      return await fetchTaskDetails(approval.taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Не удалось подтвердить результат публикации. Обновите статус операции.'))
    }
  },
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

export const cancelHITLOperation = createAsyncThunk(
  'hitl/cancelOperation',
  async ({ taskId, operationId }: { taskId: string; operationId: string }, { rejectWithValue }) => {
    try {
      await cancelOperation(taskId, operationId)
      return await fetchTaskDetails(taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Не удалось отменить операцию. Возможно, публикация уже началась.'))
    }
  },
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
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
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
      .addCase(applyHITLTask.fulfilled, (state, action) => {
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(applyHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : 'Не удалось получить результат операции'
      })
      .addCase(cancelHITLOperation.fulfilled, (state, action) => {
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(cancelHITLOperation.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : 'Не удалось отменить операцию'
      })
  },
})

export default hitlSlice.reducer
