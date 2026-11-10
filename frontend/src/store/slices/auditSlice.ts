import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import type { AuditRequest, AuditStatus } from '@/types/audit'
import { submitAuditRequest, getAuditStatus, cancelAudit, classifyAuditError } from '@/api/publicAuditAPI'
import { getApiErrorMessage } from '@/api/authAPI'
import { parseApiError } from '@/utils/apiError'
import { describeProjectError } from '@/utils/projectErrors'
import {
  describeAuditLoadError,
  isPermanentAuditError,
  isTerminalAuditStatus,
  type AuditLoadErrorKind,
} from '@/utils/auditStatus'

interface AuditState {
  currentAudit: AuditStatus | null
  // uid, который сейчас открыт на экране. Ответы по другому uid отбрасываются (быстрая смена маршрута).
  activeUid: string | null
  // Идентификатор последнего запуска: поздний ответ предыдущего запуска не затирает новый.
  startRequestId: string | null
  loading: boolean
  cancelling: boolean
  error: string | null
  // Тип ошибки загрузки: по нему экран различает «не найден», «истёк», «нет связи», «таймаут».
  errorKind: AuditLoadErrorKind | null
  // Код ошибки API (например domain_not_verified): по нему экран предлагает следующий шаг.
  errorCode: string | null
  polling: boolean
}

interface AuditRejection {
  message: string
  kind: AuditLoadErrorKind
  code?: string
}

const initialState: AuditState = {
  currentAudit: null,
  activeUid: null,
  startRequestId: null,
  loading: false,
  cancelling: false,
  error: null,
  errorKind: null,
  errorCode: null,
  polling: false,
}

export const startAudit = createAsyncThunk<AuditStatus, AuditRequest, { rejectValue: AuditRejection }>(
  'audit/start',
  async (request, { rejectWithValue }) => {
    try {
      return await submitAuditRequest(request)
    } catch (error) {
      const parsed = parseApiError(error)
      return rejectWithValue({
        message: describeProjectError(parsed, getApiErrorMessage(error, 'Не удалось запустить аудит.')),
        // Ошибка запуска — не «аудит не найден»: тип загрузки здесь не различается.
        kind: 'error',
        code: parsed.code,
      })
    }
  },
)

export const pollAuditStatus = createAsyncThunk<AuditStatus, string, { rejectValue: AuditRejection }>(
  'audit/pollStatus',
  async (uid, { rejectWithValue, signal }) => {
    try {
      return await getAuditStatus(uid, signal)
    } catch (error) {
      const kind = classifyAuditError(error)
      return rejectWithValue({
        message: describeAuditLoadError(kind, getApiErrorMessage(error, 'Не удалось получить статус аудита.')),
        kind,
      })
    }
  },
)

export const cancelAuditRequest = createAsyncThunk<AuditStatus, string, { rejectValue: AuditRejection }>(
  'audit/cancel',
  async (uid, { rejectWithValue }) => {
    try {
      return await cancelAudit(uid)
    } catch (error) {
      return rejectWithValue({
        message: getApiErrorMessage(error, 'Не удалось отменить аудит.'),
        kind: classifyAuditError(error),
      })
    }
  },
)

const auditSlice = createSlice({
  name: 'audit',
  initialState,
  reducers: {
    resetAudit: () => initialState,
    // Экран открыл аудит uid: данные другого аудита не показываются, пока не придёт ответ по этому.
    selectAudit: (state, action: PayloadAction<string>) => {
      const uid = action.payload
      state.activeUid = uid
      state.error = null
      state.errorKind = null
      state.errorCode = null
      if (state.currentAudit?.uid !== uid) {
        state.currentAudit = null
      }
      state.polling = !isTerminalAuditStatus(state.currentAudit?.status)
    },
    setPolling: (state, action: PayloadAction<boolean>) => {
      state.polling = action.payload
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(startAudit.pending, (state, action) => {
        state.loading = true
        state.error = null
        state.errorKind = null
        state.errorCode = null
        state.startRequestId = action.meta.requestId
      })
      .addCase(startAudit.fulfilled, (state, action) => {
        if (state.startRequestId !== action.meta.requestId) {
          return
        }
        state.loading = false
        state.startRequestId = null
        state.currentAudit = action.payload
        state.activeUid = action.payload.uid
        state.polling = !isTerminalAuditStatus(action.payload.status)
      })
      .addCase(startAudit.rejected, (state, action) => {
        if (state.startRequestId !== action.meta.requestId) {
          return
        }
        state.loading = false
        state.startRequestId = null
        state.error = action.payload?.message ?? action.error.message ?? 'Не удалось запустить аудит.'
        state.errorKind = action.payload?.kind ?? 'error'
        state.errorCode = action.payload?.code ?? null
      })
      .addCase(pollAuditStatus.fulfilled, (state, action) => {
        if (action.meta.arg !== state.activeUid) {
          return
        }
        state.currentAudit = action.payload
        state.error = null
        state.errorKind = null
        state.polling = !isTerminalAuditStatus(action.payload.status)
      })
      .addCase(pollAuditStatus.rejected, (state, action) => {
        // Отмена запроса (смена маршрута, размонтирование) и ответ по чужому uid ошибкой не считаются.
        if (action.meta.aborted || action.meta.arg !== state.activeUid) {
          return
        }
        const kind = action.payload?.kind ?? 'error'
        state.error = action.payload?.message ?? action.error.message ?? 'Не удалось получить статус аудита.'
        state.errorKind = kind
        if (isPermanentAuditError(kind)) {
          state.polling = false
        }
      })
      .addCase(cancelAuditRequest.pending, (state, action) => {
        if (action.meta.arg === state.activeUid) {
          state.cancelling = true
        }
      })
      .addCase(cancelAuditRequest.fulfilled, (state, action) => {
        state.cancelling = false
        if (action.meta.arg !== state.activeUid) {
          return
        }
        state.currentAudit = action.payload
        state.polling = !isTerminalAuditStatus(action.payload.status)
        state.error = null
        state.errorKind = null
      })
      .addCase(cancelAuditRequest.rejected, (state, action) => {
        state.cancelling = false
        if (action.meta.arg !== state.activeUid) {
          return
        }
        state.error = action.payload?.message ?? action.error.message ?? 'Не удалось отменить аудит.'
        state.errorKind = action.payload?.kind ?? 'error'
      })
  },
})

export const { resetAudit, selectAudit, setPolling } = auditSlice.actions
export default auditSlice.reducer
