import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { clearError, register } from '../../store/slices/authSlice'
import Input from '../../components/ui/Input'
import Button from '../../components/ui/Button'
import { validatePassword } from '../../utils/validation'

const PENDING_PROJECT_SAVE_KEY = 'seoMaster.pendingProjectSave'

const getPendingSaveRedirect = () => {
  try {
    const pendingSave = JSON.parse(localStorage.getItem(PENDING_PROJECT_SAVE_KEY) || 'null')
    return pendingSave?.uid ? `/audit/results/${pendingSave.uid}?saveProject=1` : null
  } catch {
    return null
  }
}

const Register = () => {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
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
      setValidationError('Пароли не совпадают.')
      return
    }

    if (!passwordValidation.valid) {
      setValidationError(passwordValidation.message ?? 'Пароль не соответствует требованиям.')
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
      const redirect = searchParams.get('redirect') || getPendingSaveRedirect()
      navigate(redirect?.startsWith('/') ? redirect : '/')
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
          <p className="text-gray-600">Создайте аккаунт</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {(error || validationError) && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {validationError || error}
            </div>
          )}

          <Input
            label="Имя"
            type="text"
            name="name"
            value={formData.name}
            onChange={handleChange}
            placeholder="Иван Иванов"
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
            label="Пароль"
            type="password"
            name="password"
            value={formData.password}
            onChange={handleChange}
            placeholder="Не менее 8 символов"
            hint={formData.password ? passwordValidation.message : 'Используйте не менее 8 символов.'}
            error={formData.password && !passwordValidation.valid ? passwordValidation.message : undefined}
            required
          />

          <Input
            label="Подтвердите пароль"
            type="password"
            name="confirmPassword"
            value={formData.confirmPassword}
            onChange={handleChange}
            placeholder="Повторите пароль"
            error={
              formData.confirmPassword && formData.password !== formData.confirmPassword
                ? 'Пароли не совпадают.'
                : undefined
            }
            required
          />

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Создаём...' : 'Создать аккаунт'}
          </Button>
        </form>

        <div className="mt-6 text-center text-sm text-gray-600">
          <p>
            Уже есть аккаунт?{' '}
            <Link
              to={searchParams.get('redirect') ? `/login?redirect=${encodeURIComponent(searchParams.get('redirect') || '')}` : '/login'}
              className="font-medium text-blue-600 hover:text-blue-700"
            >
              Войти
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Register
