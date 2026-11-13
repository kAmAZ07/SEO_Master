import { describe, expect, it } from 'vitest'

import { getProjectNameFromUrl, initialProjectSaveState, projectSaveReducer, readPendingProjectSave } from './projectSave'

describe('projectSaveReducer', () => {
  it('idle → saving → done', () => {
    let state = projectSaveReducer(initialProjectSaveState, { type: 'start' })
    expect(state).toEqual({ status: 'saving' })
    state = projectSaveReducer(state, { type: 'success' })
    expect(state).toEqual({ status: 'done' })
  })

  it('ошибка не запускает повтор сама, а повторный start возможен', () => {
    let state = projectSaveReducer({ status: 'saving' }, { type: 'failure', message: 'нет связи' })
    expect(state).toEqual({ status: 'failed', message: 'нет связи' })
    state = projectSaveReducer(state, { type: 'start' })
    expect(state).toEqual({ status: 'saving' })
  })

  it('во время сохранения и после успеха новый запуск игнорируется', () => {
    const saving = { status: 'saving' } as const
    expect(projectSaveReducer(saving, { type: 'start' })).toBe(saving)
    const done = { status: 'done' } as const
    expect(projectSaveReducer(done, { type: 'start' })).toBe(done)
  })

  it('успех и ошибка вне сохранения не меняют состояние', () => {
    expect(projectSaveReducer(initialProjectSaveState, { type: 'success' })).toBe(initialProjectSaveState)
    expect(projectSaveReducer(initialProjectSaveState, { type: 'failure', message: 'x' })).toBe(initialProjectSaveState)
  })
})

describe('вспомогательные функции', () => {
  it('имя проекта из адреса', () => {
    expect(getProjectNameFromUrl('https://www.example.com/blog')).toBe('example.com')
    expect(getProjectNameFromUrl('not a url')).toBe('not a url')
  })

  it('читает отложенное сохранение и терпит мусор в хранилище', () => {
    const storage = (value: string | null) => ({ getItem: () => value })
    expect(readPendingProjectSave(storage('{"uid":"a1","url":"https://e.com"}'), 'k')).toEqual({ uid: 'a1', url: 'https://e.com' })
    expect(readPendingProjectSave(storage('{broken'), 'k')).toBeNull()
    expect(readPendingProjectSave(storage('{"uid":5}'), 'k')).toBeNull()
    expect(readPendingProjectSave(storage(null), 'k')).toBeNull()
  })
})
