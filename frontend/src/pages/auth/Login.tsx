import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { clearError, login } from '../../store/slices/authSlice'
import Input from '../../components/ui/Input'
import Button from '../../components/ui/Button'

const PENDING_PROJECT_SAVE_KEY = 'seoMaster.pendingProjectSave'

const getPendingSaveRedirect = () => {
  try {
    const pendingSave = JSON.parse(localStorage.getItem(PENDING_PROJECT_SAVE_KEY) || 'null')
    return pendingSave?.uid ? `/audit/results/${pendingSave.uid}?saveProject=1` : null
  } catch {
    return null
  }
}

const Login = () => {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const dispatch = useAppDispatch()
  const { loading, error } = useAppSelector((state) => state.auth)

  const [formData, setFormData] = useState({
    email: '',
    password: '',
  })

  useEffect(() => {
    return () => {
      dispatch(clearError())
    }
  }, [dispatch])

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    const result = await dispatch(
      login({
        email: formData.email.trim(),
        password: formData.password,
      }),
    )
    if (login.fulfilled.match(result)) {
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
          <p className="text-gray-600">Войдите в аккаунт</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

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
            placeholder="Введите пароль"
            required
          />

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Входим...' : 'Войти'}
          </Button>
        </form>

        <div className="mt-6 text-center text-sm text-gray-600">
          <p>
            Нет аккаунта?{' '}
            <Link
              to={searchParams.get('redirect') ? `/register?redirect=${encodeURIComponent(searchParams.get('redirect') || '')}` : '/register'}
              className="font-medium text-blue-600 hover:text-blue-700"
            >
              Зарегистрироваться
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default Login
