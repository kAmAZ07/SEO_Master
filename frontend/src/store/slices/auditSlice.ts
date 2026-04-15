import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import type { AuditRequest, AuditStatus } from '@/types/audit'
import { submitAuditRequest, getAuditStatus } from '@/api/publicAuditAPI'
import { getApiErrorMessage } from '@/api/authAPI'

interface AuditState {
  currentAudit: AuditStatus | null
  loading: boolean
  error: string | null
  polling: boolean
}

interface RejectedActionLike {
  payload?: unknown
  error?: {
    message?: string
  }
}

const initialState: AuditState = {
  currentAudit: null,
  loading: false,
  error: null,
  polling: false,
}

export const startAudit = createAsyncThunk(
  'audit/start',
  async (request: AuditRequest, { rejectWithValue }) => {
    try {
      return await submitAuditRequest(request)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to start the audit.'))
    }
  },
)

export const pollAuditStatus = createAsyncThunk(
  'audit/pollStatus',
  async (uid: string, { rejectWithValue }) => {
    try {
      return await getAuditStatus(uid)
    } catch (error) {
      return rejectWithValue(getApiErrorMessage(error, 'Failed to fetch the audit status.'))
    }
  },
)

const getErrorPayload = (action: RejectedActionLike, fallback: string) => {
  if (typeof action.payload === 'string' && action.payload.trim()) {
    return action.payload
  }
  if (typeof action.error?.message === 'string' && action.error.message.trim()) {
    return action.error.message
  }
  return fallback
}

const auditSlice = createSlice({
  name: 'audit',
  initialState,
  reducers: {
    resetAudit: (state) => {
      state.currentAudit = null
      state.error = null
      state.loading = false
      state.polling = false
    },
    setPolling: (state, action: PayloadAction<boolean>) => {
      state.polling = action.payload
    },
    setCurrentAudit: (state, action: PayloadAction<AuditStatus>) => {
      state.currentAudit = action.payload
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(startAudit.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(startAudit.fulfilled, (state, action) => {
        state.loading = false
        state.currentAudit = action.payload
        state.polling = true
        state.error = null
      })
      .addCase(startAudit.rejected, (state, action) => {
        state.loading = false
        state.error = getErrorPayload(action, 'Failed to start the audit.')
      })
      .addCase(pollAuditStatus.fulfilled, (state, action) => {
        state.currentAudit = action.payload
        state.error = null
        if (action.payload.status === 'completed' || action.payload.status === 'failed') {
          state.polling = false
        }
      })
      .addCase(pollAuditStatus.rejected, (state, action) => {
        state.error = getErrorPayload(action, 'Failed to fetch the audit status.')
        state.polling = false
      })
  },
})

export const { resetAudit, setPolling, setCurrentAudit } = auditSlice.actions
export default auditSlice.reducer
