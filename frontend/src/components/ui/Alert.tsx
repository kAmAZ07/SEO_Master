import { ReactNode } from 'react'

interface AlertProps {
  type?: 'info' | 'success' | 'warning' | 'error'
  children: ReactNode
  className?: string
}

const Alert = ({ type = 'info', children, className = '' }: AlertProps) => {
  const styles = {
    info: 'bg-blue-50 border-blue-200 text-blue-800',
    success: 'bg-green-50 border-green-200 text-green-800',
    warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    error: 'bg-red-50 border-red-200 text-red-800',
  }

  const icons = {
    info: 'i',
    success: 'ok',
    warning: '!',
    error: 'x',
  }

  return (
    <div className={`rounded-lg border p-4 ${styles[type]} ${className}`}>
      <div className="flex items-start gap-3">
        <span className="text-sm font-semibold uppercase">{icons[type]}</span>
        <div className="flex-1">{children}</div>
      </div>
    </div>
  )
}

export default Alert
