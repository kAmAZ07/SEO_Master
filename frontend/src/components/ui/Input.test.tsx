import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import Input from './Input'

describe('Input', () => {
  it('renders label, hint, and typed value', async () => {
    render(<Input label="Project URL" hint="Use absolute URL" />)

    const input = screen.getByLabelText('Project URL')
    await userEvent.type(input, 'https://example.com')

    expect(input).toHaveValue('https://example.com')
    expect(screen.getByText('Use absolute URL')).toBeInTheDocument()
  })

  it('toggles password visibility', async () => {
    render(<Input label="Password" type="password" />)

    const input = screen.getByLabelText('Password')
    expect(input).toHaveAttribute('type', 'password')

    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'text')
  })
})

