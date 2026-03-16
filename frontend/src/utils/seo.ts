export const calculateKeywordDensity = (text: string, keyword: string): number => {
  const words = text.toLowerCase().split(/\s+/);
  const keywordWords = keyword.toLowerCase().split(/\s+/);
  
  let count = 0;
  for (let i = 0; i <= words.length - keywordWords.length; i++) {
    const phrase = words.slice(i, i + keywordWords.length).join(' ');
    if (phrase === keywordWords.join(' ')) {
      count++;
    }
  }
  
  return (count / words.length) * 100;
};

export const analyzeReadability = (text: string): { score: number; level: string } => {
  const sentences = text.split(/[.!?]+/).filter(s => s.trim().length > 0);
  const words = text.split(/\s+/).filter(w => w.trim().length > 0);
  const syllables = words.reduce((acc, word) => acc + countSyllables(word), 0);
  
  const avgWordsPerSentence = words.length / sentences.length;
  const avgSyllablesPerWord = syllables / words.length;
  
  const score = 206.835 - 1.015 * avgWordsPerSentence - 84.6 * avgSyllablesPerWord;
  
  let level = 'Очень сложно';
  if (score >= 90) level = 'Очень легко';
  else if (score >= 80) level = 'Легко';
  else if (score >= 70) level = 'Довольно легко';
  else if (score >= 60) level = 'Стандартно';
  else if (score >= 50) level = 'Довольно сложно';
  else if (score >= 30) level = 'Сложно';
  
  return { score: Math.max(0, Math.min(100, score)), level };
};

const countSyllables = (word: string): number => {
  word = word.toLowerCase();
  if (word.length <= 3) return 1;
  word = word.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, '');
  word = word.replace(/^y/, '');
  const matches = word.match(/[aeiouy]{1,2}/g);
  return matches ? matches.length : 1;
};

export const extractMetaTags = (html: string): { title?: string; description?: string; keywords?: string } => {
  const titleMatch = html.match(/<title>(.*?)<\/title>/i);
  const descMatch = html.match(/<meta\s+name=["']description["']\s+content=["'](.*?)["']/i);
  const keywordsMatch = html.match(/<meta\s+name=["']keywords["']\s+content=["'](.*?)["']/i);
  
  return {
    title: titleMatch ? titleMatch[1] : undefined,
    description: descMatch ? descMatch[1] : undefined,
    keywords: keywordsMatch ? keywordsMatch[1] : undefined,
  };
};
