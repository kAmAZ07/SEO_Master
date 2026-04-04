import { useState } from 'react';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { updateProfile, changePassword } from '../store/slices/authSlice';
import Card from '../components/ui/Card';
import Button from '../components/ui/Button';
import Input from '../components/ui/Input';

const Settings = () => {
  const dispatch = useAppDispatch();
  const { user, loading } = useAppSelector((state) => state.auth);
  
  const [profileData, setProfileData] = useState({
    name: user?.name || '',
    email: user?.email || '',
    company: user?.company || '',
  });

  const [passwordData, setPasswordData] = useState({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  });

  const [notifications, setNotifications] = useState({
    emailReports: true,
    weeklyDigest: true,
    auditAlerts: true,
    keywordChanges: false,
  });

  const [activeTab, setActiveTab] = useState<'profile' | 'password' | 'notifications'>('profile');

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    await dispatch(updateProfile(profileData));
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordData.newPassword !== passwordData.confirmPassword) {
      alert('Пароли не совпадают');
      return;
    }
    if (passwordData.newPassword.length < 6) {
      alert('Пароль должен содержать минимум 6 символов');
      return;
    }
    await dispatch(changePassword({
      currentPassword: passwordData.currentPassword,
      newPassword: passwordData.newPassword,
    }));
    setPasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Настройки</h1>
        <p className="text-gray-600 mt-1">Управление вашим аккаунтом и настройками</p>
      </div>

      <div className="flex gap-4 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('profile')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'profile'
              ? 'text-blue-600 border-b-2 border-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Профиль
        </button>
        <button
          onClick={() => setActiveTab('password')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'password'
              ? 'text-blue-600 border-b-2 border-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Безопасность
        </button>
        <button
          onClick={() => setActiveTab('notifications')}
          className={`px-4 py-2 font-medium transition-colors ${
            activeTab === 'notifications'
              ? 'text-blue-600 border-b-2 border-blue-600'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Уведомления
        </button>
      </div>

      {activeTab === 'profile' && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Информация профиля</h2>
          <form onSubmit={handleUpdateProfile} className="space-y-4">
            <Input
              label="Имя"
              value={profileData.name}
              onChange={(e) => setProfileData({ ...profileData, name: e.target.value })}
              placeholder="Ваше имя"
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
              label="Компания"
              value={profileData.company}
              onChange={(e) => setProfileData({ ...profileData, company: e.target.value })}
              placeholder="Название компании"
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Сохранение...' : 'Сохранить изменения'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'password' && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Изменить пароль</h2>
          <form onSubmit={handleChangePassword} className="space-y-4">
            <Input
              label="Текущий пароль"
              type="password"
              value={passwordData.currentPassword}
              onChange={(e) => setPasswordData({ ...passwordData, currentPassword: e.target.value })}
              required
            />
            <Input
              label="Новый пароль"
              type="password"
              value={passwordData.newPassword}
              onChange={(e) => setPasswordData({ ...passwordData, newPassword: e.target.value })}
              placeholder="Минимум 6 символов"
              required
            />
            <Input
              label="Подтвердите новый пароль"
              type="password"
              value={passwordData.confirmPassword}
              onChange={(e) => setPasswordData({ ...passwordData, confirmPassword: e.target.value })}
              required
            />
            <Button type="submit" disabled={loading}>
              {loading ? 'Изменение...' : 'Изменить пароль'}
            </Button>
          </form>
        </Card>
      )}

      {activeTab === 'notifications' && (
        <Card>
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Настройки уведомлений</h2>
          <div className="space-y-4">
            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <h3 className="font-medium text-gray-900">Email отчеты</h3>
                <p className="text-sm text-gray-600">Получать отчеты о проектах на email</p>
              </div>
              abel className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={notifications.emailReports}
                  onChange={(e) => setNotifications({ ...notifications, emailReports: e.target.checked })}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
            </div>

            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <h3 className="font-medium text-gray-900">Еженедельный дайджест</h3>
                <p className="text-sm text-gray-600">Сводка изменений раз в неделю</p>
              </div>
              abel className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={notifications.weeklyDigest}
                  onChange={(e) => setNotifications({ ...notifications, weeklyDigest: e.target.checked })}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
            </div>

            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <h3 className="font-medium text-gray-900">Уведомления об аудитах</h3>
                <p className="text-sm text-gray-600">Получать уведомления при завершении аудита</p>
              </div>
              abel className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={notifications.auditAlerts}
                  onChange={(e) => setNotifications({ ...notifications, auditAlerts: e.target.checked })}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
            </div>

            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <h3 className="font-medium text-gray-900">Изменения позиций</h3>
                <p className="text-sm text-gray-600">Уведомления о значительных изменениях позиций</p>
              </div>
              abel className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={notifications.keywordChanges}
                  onChange={(e) => setNotifications({ ...notifications, keywordChanges: e.target.checked })}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
            </div>

            <Button onClick={() => console.log('Save notifications', notifications)}>
              Сохранить настройки
            </Button>
          </div>
        </Card>
      )}

      <Card className="border-red-200">
        <h2 className="text-xl font-semibold text-red-600 mb-4">Опасная зона</h2>
        <p className="text-gray-600 mb-4">
          Удаление аккаунта приведет к безвозвратной потере всех данных и проектов.
        </p>
        <Button className="bg-red-600 hover:bg-red-700">
          Удалить аккаунт
        </Button>
      </Card>
    </div>
  );
};

export default Settings;
