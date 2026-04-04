import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { clearError, register } from '../../store/slices/authSlice'
import Input from '../../components/ui/Input'
import Button from '../../components/ui/Button'
import { validatePassword } from '../../utils/validation'

const Register = () => {
  const navigate = useNavigate()
  const dispatch = useAppDispatch()
  const { loading, error } = useAppSelector((state) => state.auth)

  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
  })
  const [validationError, setValidationError] = useState('')

  useEffect(() => {
    return () => {
      dispatch(clearError())
    }
  }, [dispatch])

  const passwordValidation = useMemo(() => validatePassword(formData.password), [formData.password])

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setValidationError('')

    if (formData.password !== formData.confirmPassword) {
      setValidationError('Passwords do not match.')
      return
    }

    if (!passwordValidation.valid) {
      setValidationError(passwordValidation.message ?? 'Password is invalid.')
      return
    }

    const result = await dispatch(
      register({
        email: formData.email.trim(),
        password: formData.password,
        name: formData.name.trim() || undefined,
      }),
    )

    if (register.fulfilled.match(result)) {
      navigate('/')
    }
  }

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (error) {
      dispatch(clearError())
    }

    setFormData((current) => ({
      ...current,
      [event.target.name]: event.target.value,
    }))
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 px-4 py-10">
      <div className="mx-auto w-full max-w-md rounded-2xl bg-white p-8 shadow-xl">
        <div className="mb-8 text-center">
          <h1 className="mb-2 text-3xl font-bold text-blue-600">SEO Master</h1>
          <p className="text-gray-600">Create a new account</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {(error || validationError) && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {validationError || error}
            </div>
          )}

          <Input
            label="Name"
            type="text"
            name="name"
            value={formData.name}
            onChange={handleChange}
            placeholder="John Test"
          />

          <Input
            label="Email"
            type="email"
            name="email"
            value={formData.email}
            onChange={handleChange}
            placeholder="example@email.com"
            required
          />

          <Input
            label="Password"
            type="password"
            name="password"
            value={formData.password}
            onChange={handleChange}
            placeholder="At least 8 characters"
            hint={formData.password ? passwordValidation.message : 'Use at least 8 characters.'}
            error={formData.password && !passwordValidation.valid ? passwordValidation.message : undefined}
            required
          />

          <Input
            label="Confirm password"
            type="password"
            name="confirmPassword"
            value={formData.confirmPassword}
            onChange={handleChange}
            placeholder="Repeat your password"
            error={
              formData.confirmPassword && formData.password !== formData.confirmPassword
                ? 'Passwords do not match.'
                : undefined
            }
            required
          />

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Creating account...' : 'Create account'}
          </Button>
        </form>

        <div className="mt-6 text-center text-sm text-gray-600">
          <p>
            Already have an account?{' '}
            <Link to="/login" className="font-medium text-blue-600 hover:text-blue-700">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Register
