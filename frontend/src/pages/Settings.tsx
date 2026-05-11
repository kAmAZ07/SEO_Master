import { useMemo, useState } from 'react'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { updateProfile, changePassword, clearError } from '../store/slices/authSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'
import { validatePassword } from '../utils/validation'

const Settings = () => {
  const dispatch = useAppDispatch()
  const { user, loading, error } = useAppSelector((state) => state.auth)

  const [profileData, setProfileData] = useState({
    name: user?.name || '',
    email: user?.email || '',
    company: user?.company || '',
  })

  const [passwordData, setPasswordData] = useState({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  })

  const [activeTab, setActiveTab] = useState<'profile' | 'password'>('profile')
  const [localPasswordError, setLocalPasswordError] = useState('')

  const passwordValidation = useMemo(
    () => validatePassword(passwordData.newPassword),
    [passwordData.newPassword],
  )

  const handleUpdateProfile = async (event: React.FormEvent) => {
    event.preventDefault()
    await dispatch(updateProfile(profileData))
  }

  const handleChangePassword = async (event: React.FormEvent) => {
    event.preventDefault()
    setLocalPasswordError('')

    if (passwordData.newPassword !== passwordData.confirmPassword) {
      setLocalPasswordError('Пароли не совпадают.')
      return
    }

    if (!passwordValidation.valid) {
      setLocalPasswordError(passwordValidation.message ?? 'Пароль не соответствует требованиям.')
      return
    }

    const result = await dispatch(
      changePassword({
        currentPassword: passwordData.currentPassword,
        newPassword: passwordData.newPassword,
      }),
    )

    if (changePassword.fulfilled.match(result)) {
      setPasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' })
    }
  }

  const tabButtonClass = (tab: 'profile' | 'password') =>
    `px-4 py-2 font-medium transition-colors ${
      activeTab === tab ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-600 hover:text-gray-900'
    }`

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Настройки</h1>
        <p className="mt-1 text-gray-600">Управляйте профилем и паролем.</p>
      </div>

      <div className="flex gap-4 border-b border-gray-200">
        <button type="button" onClick={() => setActiveTab('profile')} className={tabButtonClass('profile')}>
          Профиль
        </button>
        <button type="button" onClick={() => setActiveTab('password')} className={tabButtonClass('password')}>
          Безопасность
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {activeTab === 'profile' && (
        <Card className="p-6">
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Информация о профиле</h2>
          <form onSubmit={handleUpdateProfile} className="space-y-4">
            <Input
              label="Имя"
              value={profileData.name}
              onChange={(event) => {
                dispatch(clearError())
                setProfileData({ ...profileData, name: event.target.value })
              }}
              placeholder="Ваше имя"
            />
            <Input
              label="Email"
              type="email"
              value={profileData.email}
              onChange={(event) => {
                dispatch(clearError())
                setProfileData({ ...profileData, email: event.target.value })
              }}
              placeholder="email@example.com"
              required
            />
            <Input
              label="Компания"
              value={profileData.company}
              onChange={(event) => setProfileData({ ...profileData, company: event.target.value })}
              placeholder="Название компании"
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Сохраняем...' : 'Сохранить'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'password' && (
        <Card className="p-6">
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Изменение пароля</h2>
          <form onSubmit={handleChangePassword} className="space-y-4">
            {localPasswordError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {localPasswordError}
              </div>
            )}

            <Input
              label="Текущий пароль"
              type="password"
              value={passwordData.currentPassword}
              onChange={(event) => {
                dispatch(clearError())
                setLocalPasswordError('')
                setPasswordData({ ...passwordData, currentPassword: event.target.value })
              }}
              required
            />
            <Input
              label="Новый пароль"
              type="password"
              value={passwordData.newPassword}
              onChange={(event) => {
                dispatch(clearError())
                setLocalPasswordError('')
                setPasswordData({ ...passwordData, newPassword: event.target.value })
              }}
              placeholder="Не менее 8 символов"
              error={passwordData.newPassword && !passwordValidation.valid ? passwordValidation.message : undefined}
              hint={passwordData.newPassword ? passwordValidation.message : 'Используйте не менее 8 символов.'}
              required
            />
            <Input
              label="Подтвердите новый пароль"
              type="password"
              value={passwordData.confirmPassword}
              onChange={(event) => {
                dispatch(clearError())
                setLocalPasswordError('')
                setPasswordData({ ...passwordData, confirmPassword: event.target.value })
              }}
              error={
                passwordData.confirmPassword && passwordData.newPassword !== passwordData.confirmPassword
                  ? 'Пароли не совпадают.'
                  : undefined
              }
              required
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Обновляем...' : 'Изменить пароль'}
            </Button>
          </form>
        </Card>
      )}
    </div>
  )
}

export default Settings
