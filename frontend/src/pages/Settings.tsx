import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import { changePassword, updateProfile } from '../store/slices/authSlice'
import Card from '../components/ui/Card'
import Button from '../components/ui/Button'
import Input from '../components/ui/Input'

const Settings = () => {
  const dispatch = useAppDispatch()
  const { user, loading } = useAppSelector((state) => state.auth)

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

  const [notifications, setNotifications] = useState({
    emailReports: true,
    weeklyDigest: true,
    auditAlerts: true,
    keywordChanges: false,
  })

  const [activeTab, setActiveTab] = useState<'profile' | 'password' | 'notifications'>('profile')

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    await dispatch(updateProfile(profileData))
  }

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()

    if (passwordData.newPassword !== passwordData.confirmPassword) {
      alert('Passwords do not match')
      return
    }

    if (passwordData.newPassword.length < 6) {
      alert('Password must contain at least 6 characters')
      return
    }

    await dispatch(
      changePassword({
        currentPassword: passwordData.currentPassword,
        newPassword: passwordData.newPassword,
      })
    )

    setPasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' })
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
        <p className="mt-1 text-gray-600">Manage your account and notification preferences.</p>
      </div>

      <div className="flex gap-4 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('profile')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'profile'
              ? 'border-b-2 border-blue-600 text-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Profile
        </button>
        <button
          onClick={() => setActiveTab('password')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'password'
              ? 'border-b-2 border-blue-600 text-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Security
        </button>
        <button
          onClick={() => setActiveTab('notifications')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'notifications'
              ? 'border-b-2 border-blue-600 text-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Notifications
        </button>
      </div>

      {activeTab === 'profile' && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Profile information</h2>
          <form onSubmit={handleUpdateProfile} className="space-y-4">
            <Input
              label="Name"
              value={profileData.name}
              onChange={(e) => setProfileData({ ...profileData, name: e.target.value })}
              placeholder="Your name"
            />
            <Input
              label="Email"
              type="email"
              value={profileData.email}
              onChange={(e) => setProfileData({ ...profileData, email: e.target.value })}
              placeholder="email@example.com"
              required
            />
            <Input
              label="Company"
              value={profileData.company}
              onChange={(e) => setProfileData({ ...profileData, company: e.target.value })}
              placeholder="Company name"
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Saving...' : 'Save changes'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'password' && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Change password</h2>
          <form onSubmit={handleChangePassword} className="space-y-4">
            <Input
              label="Current password"
              type="password"
              value={passwordData.currentPassword}
              onChange={(e) => setPasswordData({ ...passwordData, currentPassword: e.target.value })}
              required
            />
            <Input
              label="New password"
              type="password"
              value={passwordData.newPassword}
              onChange={(e) => setPasswordData({ ...passwordData, newPassword: e.target.value })}
              placeholder="At least 6 characters"
              required
            />
            <Input
              label="Confirm new password"
              type="password"
              value={passwordData.confirmPassword}
              onChange={(e) => setPasswordData({ ...passwordData, confirmPassword: e.target.value })}
              required
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Updating...' : 'Update password'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'notifications' && (
        <Card>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Notification settings</h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
              <div>
                <h3 className="font-medium text-gray-900">Email reports</h3>
                <p className="text-sm text-gray-600">Receive project reports by email.</p>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  checked={notifications.emailReports}
                  onChange={(e) => setNotifications({ ...notifications, emailReports: e.target.checked })}
                  className="peer sr-only"
                />
                <div className="h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-gray-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300"></div>
              </label>
            </div>

            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
              <div>
                <h3 className="font-medium text-gray-900">Weekly digest</h3>
                <p className="text-sm text-gray-600">Get a weekly summary of changes.</p>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  checked={notifications.weeklyDigest}
                  onChange={(e) => setNotifications({ ...notifications, weeklyDigest: e.target.checked })}
                  className="peer sr-only"
                />
                <div className="h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-gray-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300"></div>
              </label>
            </div>

            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
              <div>
                <h3 className="font-medium text-gray-900">Audit alerts</h3>
                <p className="text-sm text-gray-600">Notify when an audit run is completed.</p>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  checked={notifications.auditAlerts}
                  onChange={(e) => setNotifications({ ...notifications, auditAlerts: e.target.checked })}
                  className="peer sr-only"
                />
                <div className="h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-gray-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300"></div>
              </label>
            </div>

            <div className="flex items-center justify-between rounded-lg bg-gray-50 p-4">
              <div>
                <h3 className="font-medium text-gray-900">Keyword position changes</h3>
                <p className="text-sm text-gray-600">Alert on significant ranking position changes.</p>
              </div>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  checked={notifications.keywordChanges}
                  onChange={(e) => setNotifications({ ...notifications, keywordChanges: e.target.checked })}
                  className="peer sr-only"
                />
                <div className="h-6 w-11 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-gray-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300"></div>
              </label>
            </div>

            <Button onClick={() => console.log('Save notifications', notifications)}>Save notification settings</Button>
          </div>
        </Card>
      )}

      <Card className="border-red-200">
        <h2 className="mb-4 text-xl font-semibold text-red-600">Danger zone</h2>
        <p className="mb-4 text-gray-600">Deleting your account permanently removes all projects and data.</p>
        <Button className="bg-red-600 hover:bg-red-700">Delete account</Button>
      </Card>
    </div>
  )
}

export default Settings
