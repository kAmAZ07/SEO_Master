import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
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

  const [activeTab, setActiveTab] = useState<'profile' | 'password' | 'notifications'>('profile')
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
      setLocalPasswordError('Passwords do not match.')
      return
    }

    if (!passwordValidation.valid) {
      setLocalPasswordError(passwordValidation.message ?? 'Password is invalid.')
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

  const tabButtonClass = (tab: 'profile' | 'password' | 'notifications') =>
    `px-4 py-2 font-medium transition-colors ${
      activeTab === tab ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-600 hover:text-gray-900'
    }`

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-gray-600">Manage your profile, password, and notifications.</p>
      </div>

      <div className="flex gap-4 border-b border-gray-200">
        <button type="button" onClick={() => setActiveTab('profile')} className={tabButtonClass('profile')}>
          Profile
        </button>
        <button type="button" onClick={() => setActiveTab('password')} className={tabButtonClass('password')}>
          Security
        </button>
        <button type="button" onClick={() => setActiveTab('notifications')} className={tabButtonClass('notifications')}>
          Notifications
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {activeTab === 'profile' && (
        <Card className="p-6">
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Profile information</h2>
          <form onSubmit={handleUpdateProfile} className="space-y-4">
            <Input
              label="Name"
              value={profileData.name}
              onChange={(event) => {
                dispatch(clearError())
                setProfileData({ ...profileData, name: event.target.value })
              }}
              placeholder="Your name"
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
              label="Company"
              value={profileData.company}
              onChange={(event) => setProfileData({ ...profileData, company: event.target.value })}
              placeholder="Company name"
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Saving...' : 'Save changes'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'password' && (
        <Card className="p-6">
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Change password</h2>
          <form onSubmit={handleChangePassword} className="space-y-4">
            {localPasswordError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {localPasswordError}
              </div>
            )}

            <Input
              label="Current password"
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
              label="New password"
              type="password"
              value={passwordData.newPassword}
              onChange={(event) => {
                dispatch(clearError())
                setLocalPasswordError('')
                setPasswordData({ ...passwordData, newPassword: event.target.value })
              }}
              placeholder="At least 8 characters"
              error={passwordData.newPassword && !passwordValidation.valid ? passwordValidation.message : undefined}
              hint={passwordData.newPassword ? passwordValidation.message : 'Use at least 8 characters.'}
              required
            />
            <Input
              label="Confirm new password"
              type="password"
              value={passwordData.confirmPassword}
              onChange={(event) => {
                dispatch(clearError())
                setLocalPasswordError('')
                setPasswordData({ ...passwordData, confirmPassword: event.target.value })
              }}
              error={
                passwordData.confirmPassword && passwordData.newPassword !== passwordData.confirmPassword
                  ? 'Passwords do not match.'
                  : undefined
              }
              required
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Updating...' : 'Update password'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'notifications' && (
        <Card className="p-6">
          <h2 className="text-xl font-semibold text-gray-900">Уведомления</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
            Сейчас в личном кабинете включены внутренние уведомления о HITL-согласованиях: счетчик в шапке показывает
            количество ожидающих задач и ведет на экран ручной проверки. Email-уведомления и дайджесты не подключены.
          </p>
          <div className="mt-5 rounded-lg border border-blue-100 bg-blue-50 p-4">
            <h3 className="font-medium text-blue-950">HITL-согласования</h3>
            <p className="mt-1 text-sm leading-6 text-blue-800">
              Новые задачи на проверку появляются в разделе HITL и требуют ручного решения перед публикацией изменений.
            </p>
            <Link to="/dashboard/hitl" className="mt-4 inline-flex">
              <Button type="button">Открыть HITL</Button>
            </Link>
          </div>
        </Card>
      )}
    </div>
  )
}

export default Settings
