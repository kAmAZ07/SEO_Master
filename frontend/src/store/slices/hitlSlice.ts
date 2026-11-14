import { createSlice, createAsyncThunk, createAction, isAnyOf } from '@reduxjs/toolkit'
import type { HITLTask, HITLApproval } from '@/types/hitl'
import { fetchHITLTasks, fetchTaskDetails, approveTask, rejectTask, applyTask, cancelOperation, rollbackOperation } from '@/api/hitlAPI'
import { getApiErrorMessage } from '@/api/authAPI'

interface HITLState {
  tasks: HITLTask[]
  loading: boolean
  error: string | null
  listRequestId: string | null
  listGeneration: number
  commandReads: Record<string, number>
}

const initialState: HITLState = {
  tasks: [],
  loading: false,
  error: null,
  listRequestId: null,
  listGeneration: 0,
  commandReads: {},
}

const commandReadStarted = createAction<string>('hitl/commandReadStarted')

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
  async (approval: HITLApproval, { rejectWithValue, dispatch, requestId }) => {
    try {
      await approveTask(approval)
      dispatch(commandReadStarted(requestId))
      return await fetchTaskDetails(approval.taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to approve HITL task.'))
    }
  }
)

export const applyHITLTask = createAsyncThunk(
  'hitl/apply',
  async ({ approval, operationId }: { approval: HITLApproval; operationId: string }, { rejectWithValue, dispatch, requestId }) => {
    try {
      await applyTask(approval, operationId)
      dispatch(commandReadStarted(requestId))
      return await fetchTaskDetails(approval.taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Не удалось подтвердить результат публикации. Обновите статус операции.'))
    }
  },
)

export const rejectHITLTask = createAsyncThunk(
  'hitl/reject',
  async (approval: HITLApproval, { rejectWithValue, dispatch, requestId }) => {
    try {
      await rejectTask(approval)
      dispatch(commandReadStarted(requestId))
      return await fetchTaskDetails(approval.taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to reject HITL task.'))
    }
  }
)

export const cancelHITLOperation = createAsyncThunk(
  'hitl/cancelOperation',
  async ({ taskId, operationId }: { taskId: string; operationId: string }, { rejectWithValue, dispatch, requestId }) => {
    try {
      await cancelOperation(taskId, operationId)
      dispatch(commandReadStarted(requestId))
      return await fetchTaskDetails(taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Не удалось отменить операцию. Возможно, публикация уже началась.'))
    }
  },
)

export const rollbackHITLOperation = createAsyncThunk(
  'hitl/rollback',
  async ({ taskId, originalId, operationId, reason }: { taskId: string; originalId: string; operationId: string; reason: string }, { rejectWithValue, dispatch, requestId }) => {
    try {
      await rollbackOperation(taskId, originalId, operationId, reason)
      dispatch(commandReadStarted(requestId))
      return await fetchTaskDetails(taskId)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Не удалось подтвердить результат отката. Обновите статус операции.'))
    }
  },
)

const hitlSlice = createSlice({
  name: 'hitl',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(commandReadStarted, (state, action) => {
        state.commandReads[action.payload] = state.listGeneration
        state.listRequestId = null
        state.loading = false
      })
      .addCase(loadHITLTasks.pending, (state, action) => {
        state.loading = true
        state.error = null
        state.listRequestId = action.meta.requestId
      })
      .addCase(loadHITLTasks.fulfilled, (state, action) => {
        if (state.listRequestId !== action.meta.requestId) return
        state.loading = false
        state.listRequestId = null
        state.tasks = action.payload
        state.listGeneration += 1
      })
      .addCase(loadHITLTasks.rejected, (state, action) => {
        if (state.listRequestId !== action.meta.requestId) return
        state.loading = false
        state.listRequestId = null
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to load tasks'
      })
      .addCase(approveHITLTask.fulfilled, (state, action) => {
        if (state.commandReads[action.meta.requestId] !== state.listGeneration) return
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(approveHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to approve task'
      })
      .addCase(rejectHITLTask.fulfilled, (state, action) => {
        if (state.commandReads[action.meta.requestId] !== state.listGeneration) return
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(rejectHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : action.error.message || 'Failed to reject task'
      })
      .addCase(applyHITLTask.fulfilled, (state, action) => {
        if (state.commandReads[action.meta.requestId] !== state.listGeneration) return
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(applyHITLTask.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : 'Не удалось получить результат операции'
      })
      .addCase(cancelHITLOperation.fulfilled, (state, action) => {
        if (state.commandReads[action.meta.requestId] !== state.listGeneration) return
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(cancelHITLOperation.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : 'Не удалось отменить операцию'
      })
      .addCase(rollbackHITLOperation.fulfilled, (state, action) => {
        if (state.commandReads[action.meta.requestId] !== state.listGeneration) return
        state.error = null
        state.tasks = state.tasks.map(task => task.taskId === action.payload.taskId ? action.payload : task)
      })
      .addCase(rollbackHITLOperation.rejected, (state, action) => {
        state.error = typeof action.payload === 'string' ? action.payload : 'Не удалось подтвердить откат'
      })
      .addMatcher(isAnyOf(
        approveHITLTask.pending, approveHITLTask.fulfilled, approveHITLTask.rejected,
        rejectHITLTask.pending, rejectHITLTask.fulfilled, rejectHITLTask.rejected,
        applyHITLTask.pending, applyHITLTask.fulfilled, applyHITLTask.rejected,
        cancelHITLOperation.pending, cancelHITLOperation.fulfilled, cancelHITLOperation.rejected,
        rollbackHITLOperation.pending, rollbackHITLOperation.fulfilled, rollbackHITLOperation.rejected,
      ), (state, action) => {
        if (action.type.endsWith('/pending')) {
          // The post-command read boundary clears older reads once more after commit.
          state.listRequestId = null
          state.loading = false
        } else {
          // A list begun after the command's detail read must be allowed to finish.
          delete state.commandReads[action.meta.requestId]
        }
      })
  },
})

export default hitlSlice.reducer
