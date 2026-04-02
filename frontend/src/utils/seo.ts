export const calculateKeywordDensity = (text: string, keyword: string): number => {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean)
  const keywordWords = keyword.toLowerCase().split(/\s+/).filter(Boolean)

  if (words.length === 0 || keywordWords.length === 0) {
    return 0
  }

  let count = 0
  for (let index = 0; index <= words.length - keywordWords.length; index++) {
    const phrase = words.slice(index, index + keywordWords.length).join(' ')
    if (phrase === keywordWords.join(' ')) {
      count++
    }
  }

  return (count / words.length) * 100
}

export const analyzeReadability = (text: string): { score: number; level: string } => {
  const sentences = text
    .split(/[.!?]+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean)
  const words = text
    .split(/\s+/)
    .map((word) => word.trim())
    .filter(Boolean)

  if (sentences.length === 0 || words.length === 0) {
    return { score: 0, level: 'Very hard' }
  }

  const syllables = words.reduce((accumulator, word) => accumulator + countSyllables(word), 0)

  const avgWordsPerSentence = words.length / sentences.length
  const avgSyllablesPerWord = syllables / words.length
  const score = 206.835 - 1.015 * avgWordsPerSentence - 84.6 * avgSyllablesPerWord

  let level = 'Very hard'
  if (score >= 90) {
    level = 'Very easy'
  } else if (score >= 80) {
    level = 'Easy'
  } else if (score >= 70) {
    level = 'Fairly easy'
  } else if (score >= 60) {
    level = 'Standard'
  } else if (score >= 50) {
    level = 'Fairly hard'
  } else if (score >= 30) {
    level = 'Hard'
  }

  return { score: Math.max(0, Math.min(100, score)), level }
}

const countSyllables = (word: string): number => {
  const normalizedWord = word.toLowerCase()
  if (normalizedWord.length <= 3) {
    return 1
  }

  let cleanedWord = normalizedWord.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, '')
  cleanedWord = cleanedWord.replace(/^y/, '')

  const matches = cleanedWord.match(/[aeiouy]{1,2}/g)
  return matches ? matches.length : 1
}

export const extractMetaTags = (
  html: string
): { title?: string; description?: string; keywords?: string } => {
  const titleMatch = html.match(/<title>(.*?)<\/title>/i)
  const descriptionMatch = html.match(/<meta\s+name=["']description["']\s+content=["'](.*?)["']/i)
  const keywordsMatch = html.match(/<meta\s+name=["']keywords["']\s+content=["'](.*?)["']/i)

  return {
    title: titleMatch ? titleMatch[1] : undefined,
    description: descriptionMatch ? descriptionMatch[1] : undefined,
    keywords: keywordsMatch ? keywordsMatch[1] : undefined,
  }
}
