import { forwardRef, useId, useState } from 'react'
import type { InputHTMLAttributes } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { cn } from '@/utils/classNames'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  error?: string
  hint?: string
  containerClassName?: string
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, containerClassName, type = 'text', label, error, hint, id, disabled, ...props }, ref) => {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const isPasswordField = type === 'password'
    const [isPasswordVisible, setIsPasswordVisible] = useState(false)
    const resolvedType = isPasswordField && isPasswordVisible ? 'text' : type

    return (
      <div className={cn('space-y-1.5', containerClassName)}>
        {label && (
          <label htmlFor={inputId} className="block text-sm font-medium text-gray-700">
            {label}
          </label>
        )}

        <div className="relative">
          <input
            id={inputId}
            type={resolvedType}
            disabled={disabled}
            aria-invalid={error ? 'true' : 'false'}
            className={cn(
              'flex h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 transition-colors',
              'placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20',
              'disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-500',
              isPasswordField && 'pr-11',
              error && 'border-red-400 focus:border-red-500 focus:ring-red-500/20',
              className,
            )}
            ref={ref}
            {...props}
          />

          {isPasswordField && (
            <button
              type="button"
              onClick={() => setIsPasswordVisible((value) => !value)}
              className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-gray-500 transition-colors hover:text-gray-700"
              aria-label={isPasswordVisible ? 'Hide password' : 'Show password'}
              tabIndex={-1}
            >
              {isPasswordVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          )}
        </div>

        {error ? (
          <p className="text-sm text-red-600">{error}</p>
        ) : hint ? (
          <p className="text-sm text-gray-500">{hint}</p>
        ) : null}
      </div>
    )
  },
)

Input.displayName = 'Input'

export default Input
