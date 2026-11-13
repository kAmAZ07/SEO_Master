// Состояние сохранения публичного аудита как проекта (явный автомат вместо useEffect-цикла).
//
// idle   — ничего не делалось;
// saving — запрос создания проекта в пути (повторный запуск запрещён);
// failed — запрос завершился ошибкой; автоповтора нет, пользователь нажимает «Повторить»;
// done   — проект создан.
export type ProjectSaveState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'failed'; message: string }
  | { status: 'done' }

export type ProjectSaveAction =
  | { type: 'start' }
  | { type: 'success' }
  | { type: 'failure'; message: string }

export const initialProjectSaveState: ProjectSaveState = { status: 'idle' }

export const projectSaveReducer = (state: ProjectSaveState, action: ProjectSaveAction): ProjectSaveState => {
  switch (action.type) {
    case 'start':
      // Пока запрос идёт или проект уже создан, новый запуск состояние не меняет.
      return state.status === 'saving' || state.status === 'done' ? state : { status: 'saving' }
    case 'success':
      return state.status === 'saving' ? { status: 'done' } : state
    case 'failure':
      return state.status === 'saving' ? { status: 'failed', message: action.message } : state
    default:
      return state
  }
}

export const getProjectNameFromUrl = (value: string): string => {
  try {
    return new URL(value).hostname.replace(/^www\./, '') || value
  } catch {
    return value.replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0] || 'SEO project'
  }
}

export interface PendingProjectSave {
  uid: string
  url?: string
}

export const readPendingProjectSave = (storage: Pick<Storage, 'getItem'>, key: string): PendingProjectSave | null => {
  try {
    const parsed = JSON.parse(storage.getItem(key) || 'null')
    return parsed && typeof parsed.uid === 'string' && parsed.uid
      ? { uid: parsed.uid, url: typeof parsed.url === 'string' ? parsed.url : undefined }
      : null
  } catch {
    return null
  }
}
