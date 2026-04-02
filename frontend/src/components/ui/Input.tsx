import { InputHTMLAttributes, forwardRef, useId } from 'react'
import { cn } from '@/lib/utils'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, label, id, ...props }, ref) => {
    const generatedId = useId()
    const inputId = id ?? generatedId

    const inputElement = (
      <input
        id={inputId}
        type={type}
        className={cn(
          'flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
          className
        )}
        ref={ref}
        {...props}
      />
    )

    if (!label) {
      return inputElement
    }

    return (
      <div className="space-y-2">
        <label htmlFor={inputId} className="text-sm font-medium text-foreground">
          {label}
        </label>
        {inputElement}
      </div>
    )
  }
)

Input.displayName = 'Input'

export default Input
