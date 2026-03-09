import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit'
import { AuditRequest, AuditStatus } from '@/types/audit'
import { submitAuditRequest, getAuditStatus } from '@/api/publicAuditAPI'

interface AuditState {
  currentAudit: AuditStatus | null
  loading: boolean
  error: string | null
  polling: boolean
}

const initialState: AuditState = {
  currentAudit: null,
  loading: false,
  error: null,
  polling: false,
}

export const startAudit = createAsyncThunk(
  'audit/start',
  async (request: AuditRequest) => {
    const response = await submitAuditRequest(request)
    return response
  }
)

export const pollAuditStatus = createAsyncThunk(
  'audit/pollStatus',
  async (uid: string) => {
    const response = await getAuditStatus(uid)
    return response
  }
)

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
      })
      .addCase(startAudit.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to start audit'
      })
      .addCase(pollAuditStatus.fulfilled, (state, action) => {
        state.currentAudit = action.payload
        if (action.payload.status === 'completed' || action.payload.status === 'failed') {
          state.polling = false
        }
      })
      .addCase(pollAuditStatus.rejected, (state, action) => {
        state.error = action.error.message || 'Failed to fetch audit status'
        state.polling = false
      })
  },
})

export const { resetAudit, setPolling } = auditSlice.actions
export default auditSlice.reducer
