import { createAsyncThunk, createSlice, PayloadAction } from '@reduxjs/toolkit'
import { AuditRequest, AuditStatus } from '@/types/audit'
import {
  fetchAuditHistory as fetchAuditHistoryAPI,
  getAuditStatus,
  submitAuditRequest,
} from '@/api/publicAuditAPI'


interface AuditState {
  currentAudit: AuditStatus | null
  history: AuditStatus[]
  loading: boolean
  error: string | null
  polling: boolean
}


const initialState: AuditState = {
  currentAudit: null,
  history: [],
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


export const fetchAuditHistory = createAsyncThunk(
  'audit/fetchHistory',
  async (projectId?: string | number) => {
    return await fetchAuditHistoryAPI(projectId)
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

      .addCase(fetchAuditHistory.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchAuditHistory.fulfilled, (state, action) => {
        state.loading = false
        state.history = action.payload
      })
      .addCase(fetchAuditHistory.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to fetch audit history'
      })
  },
})


export const { resetAudit, setPolling, setCurrentAudit } = auditSlice.actions
export default auditSlice.reducer
