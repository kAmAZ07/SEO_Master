import { describe, expect, it } from 'vitest'
import { serializeAuditCsv } from './auditCsv'

describe('Audit CSV', () => {
  it.each(['=метка', '+метка', '-метка', '@метка', ' ＝метка', '\uFEFF＠метка',
    '\tметка', '\rметка', '\nметка', '\0метка', "'=метка"])('writes %j as text', (value) => {
    expect(serializeAuditCsv([[value]])).toBe('\uFEFF"\'' + value + '"')
  })

  it('preserves BOM, cell boundaries, line endings, and ordinary text', () => {
    expect(serializeAuditCsv([['Текст, "кавычки"; продолжение', ''], ['0', 'ссылка']]))
      .toBe('\uFEFF"Текст, ""кавычки""; продолжение",""\r\n"0","ссылка"')
  })
})
