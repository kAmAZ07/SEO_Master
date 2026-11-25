const formulaPrefixes = '=+-@＝＋－＠\0'

function quoteCell(value: string): string {
  // Prefix existing apostrophe-escaped text too, so distinct inputs stay distinct.
  const afterQuotes = value.replace(/^'+/, '')
  const text = afterQuotes.trimStart()
  const leadingWhitespace = afterQuotes.slice(0, afterQuotes.length - text.length)
  const isFormulaLike = (text[0] !== undefined && formulaPrefixes.includes(text[0])) ||
    /[\t\r\n]/.test(leadingWhitespace)
  const safe = isFormulaLike ? "'" + value : value
  return '"' + safe.replace(/"/g, '""') + '"'
}

export function serializeAuditCsv(rows: readonly (readonly string[])[]): string {
  return '\uFEFF' + rows.map((row) => row.map(quoteCell).join(',')).join('\r\n')
}
