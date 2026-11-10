import type { FFScore } from '../../types/dashboard'
import { describeFFScore } from '../../utils/ffScore'
import Card from '../ui/Card'

const COMPONENTS: Array<{ key: 'freshness' | 'familiarity' | 'quality'; label: string }> = [
  { key: 'freshness', label: 'Свежесть' },
  { key: 'familiarity', label: 'Известность' },
  { key: 'quality', label: 'Качество' },
]

const formatComponent = (value: number | null) => (value === null ? 'нет данных' : `${Math.round(value)}/100`)

// Карточка FF-Score проекта. Полный балл — эвристика; недоступное значение показывается словами, а не нулём,
// частичная оценка подписана отдельно и не выдаётся за полный балл.
const ProjectFFScore = ({ score }: { score?: FFScore | null }) => {
  const partialScore = typeof score?.partial?.score === 'number' ? Math.round(score.partial.score) : null

  return (
    <Card className="p-6">
      <section aria-labelledby="ffscore-title">
        <h2 id="ffscore-title" className="text-xl font-semibold text-gray-900">FF-Score</h2>
        <p className="mt-3 text-2xl font-bold text-gray-900">{describeFFScore(score)}</p>

        {score && (
          <>
            <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
              {COMPONENTS.map((item) => (
                <div key={item.key} className="rounded-lg bg-gray-50 p-3">
                  <dt className="text-xs uppercase tracking-wide text-gray-500">{item.label}</dt>
                  <dd className="mt-1 text-lg font-semibold text-gray-900">{formatComponent(score[item.key])}</dd>
                </div>
              ))}
            </dl>

            {partialScore !== null && (
              <p className="mt-3 text-sm text-gray-700">
                Частичная оценка по доступным данным: {partialScore}/100. Она не сопоставима с полным баллом.
              </p>
            )}

            <p className="mt-3 text-xs text-gray-500">Версия формулы: {score.formulaVersion || 'не указана'}</p>
          </>
        )}
      </section>
    </Card>
  )
}

export default ProjectFFScore
