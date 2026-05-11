import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { logout } from '../store/slices/authSlice';
import { loadHITLTasks } from '../store/slices/hitlSlice';
import { Bell } from 'lucide-react';

const Header = () => {
  const dispatch = useAppDispatch();
  const { user } = useAppSelector((state) => state.auth);
  const { tasks } = useAppSelector((state) => state.hitl);
  const pendingHITLCount = tasks.filter((task) => task.status === 'pending').length;
  const notificationLabel = pendingHITLCount > 0
    ? `HITL-согласования: ${pendingHITLCount}`
    : 'Нет новых HITL-согласований';

  useEffect(() => {
    void dispatch(loadHITLTasks());

    const timer = window.setInterval(() => {
      void dispatch(loadHITLTasks());
    }, 60000);

    return () => {
      window.clearInterval(timer);
    };
  }, [dispatch]);

  const handleLogout = () => {
    dispatch(logout());
  };

  return (
    <header className="fixed top-0 left-0 right-0 h-16 bg-white border-b border-gray-200 z-50">
      <div className="h-full px-6 flex items-center justify-between">
        <Link to="/" className="flex items-center">
          <h1 className="text-2xl font-bold text-blue-600">SEO Master</h1>
        </Link>
        
        <div className="flex items-center gap-4">
          <Link
            to="/dashboard/hitl"
            aria-label={notificationLabel}
            title={notificationLabel}
            className="relative flex h-10 w-10 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-700"
          >
            <span className="text-xl">
              <Bell className="h-5 w-5" />
            </span>
            {pendingHITLCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-xs font-semibold leading-none text-white">
                {pendingHITLCount > 9 ? '9+' : pendingHITLCount}
              </span>
            )}
          </Link>
          
          <div className="flex items-center gap-3 border-l border-gray-200 pl-4">
            <div className="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-semibold">
              {user?.email ? user.email[0].toUpperCase() : 'U'}
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-medium text-gray-900">{user?.email || 'Пользователь'}</span>
              <button
                onClick={handleLogout}
                className="text-xs text-gray-500 hover:text-gray-700 text-left"
              >
                Выйти
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;
