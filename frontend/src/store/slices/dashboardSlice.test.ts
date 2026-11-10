import { describe, expect, it } from 'vitest'

import reducer, { clearCurrentProject, createProject, loadProjectDetails, loadProjects } from './dashboardSlice'
import type { Project } from '@/types/dashboard'

const project = (id: string, name = id): Project => ({ id, name, url: `https://${id}.example`, status: 'active' })
const initial = reducer(undefined, { type: 'init' })

describe('dashboardSlice: гонки загрузок', () => {
  it('ответ по прежнему проекту не подменяет карточку нового', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, loadProjectDetails.pending('r2', 'p2'))
    state = reducer(state, loadProjectDetails.fulfilled(project('p1'), 'r1', 'p1'))
    expect(state.currentProject).toBeNull()
    expect(state.detailsLoading).toBe(true)

    state = reducer(state, loadProjectDetails.fulfilled(project('p2'), 'r2', 'p2'))
    expect(state.currentProject?.id).toBe('p2')
    expect(state.detailsLoading).toBe(false)
  })

  it('смена проекта сразу убирает прежнюю карточку', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, loadProjectDetails.fulfilled(project('p1'), 'r1', 'p1'))
    state = reducer(state, loadProjectDetails.pending('r2', 'p2'))
    expect(state.currentProject).toBeNull()
  })

  it('повторная загрузка того же проекта карточку не прячет', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, loadProjectDetails.fulfilled(project('p1'), 'r1', 'p1'))
    state = reducer(state, loadProjectDetails.pending('r2', 'p1'))
    expect(state.currentProject?.id).toBe('p1')
  })

  it('ошибка прежнего запроса не затирает успешную карточку', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, loadProjectDetails.pending('r2', 'p2'))
    state = reducer(state, loadProjectDetails.fulfilled(project('p2'), 'r2', 'p2'))
    state = reducer(state, loadProjectDetails.rejected(null, 'r1', 'p1', { message: 'late', notFound: false }))
    expect(state.detailsError).toBeNull()
    expect(state.currentProject?.id).toBe('p2')
  })

  it('404 отличается от прочих ошибок и убирает карточку', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, loadProjectDetails.rejected(null, 'r1', 'p1', { message: 'nf', notFound: true }))
    expect(state.detailsNotFound).toBe(true)
    expect(state.currentProject).toBeNull()

    state = reducer(state, loadProjectDetails.pending('r2', 'p1'))
    state = reducer(state, loadProjectDetails.rejected(null, 'r2', 'p1', { message: 'сеть', notFound: false }))
    expect(state.detailsNotFound).toBe(false)
    expect(state.detailsError).toBe('сеть')
  })

  it('ошибка списка проектов не смешивается с ошибкой карточки', () => {
    let state = reducer(initial, loadProjects.pending('l1'))
    state = reducer(state, loadProjects.rejected(null, 'l1', undefined, 'список не загрузился'))
    expect(state.error).toBe('список не загрузился')
    expect(state.detailsError).toBeNull()
  })

  it('устаревший ответ списка отбрасывается', () => {
    let state = reducer(initial, loadProjects.pending('l1'))
    state = reducer(state, loadProjects.pending('l2'))
    state = reducer(state, loadProjects.fulfilled([project('old')], 'l1', undefined))
    expect(state.projects).toEqual([])
    state = reducer(state, loadProjects.fulfilled([project('new')], 'l2', undefined))
    expect(state.projects.map((item) => item.id)).toEqual(['new'])
  })

  it('идемпотентное создание не дублирует проект в списке', () => {
    let state = reducer(initial, loadProjects.pending('l1'))
    state = reducer(state, loadProjects.fulfilled([project('p1', 'старое'), project('p2')], 'l1', undefined))
    state = reducer(state, createProject.fulfilled(project('p1', 'новое'), 'c1', { name: 'n', url: 'u' }))
    expect(state.projects.map((item) => item.id)).toEqual(['p1', 'p2'])
    expect(state.projects[0].name).toBe('новое')
  })

  it('clearCurrentProject сбрасывает карточку и ожидание', () => {
    let state = reducer(initial, loadProjectDetails.pending('r1', 'p1'))
    state = reducer(state, clearCurrentProject())
    expect(state.detailsLoading).toBe(false)
    state = reducer(state, loadProjectDetails.fulfilled(project('p1'), 'r1', 'p1'))
    expect(state.currentProject).toBeNull()
  })
})
