import { Link } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { logout } from '../store/slices/authSlice';

const Header = () => {
  const dispatch = useAppDispatch();
  const { user } = useAppSelector((state) => state.auth);

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
          <button className="p-2 text-gray-400 hover:text-gray-600 relative">
            <span className="text-xl">🔔</span>
            <span className="absolute top-1 right-1 h-2 w-2 bg-red-500 rounded-full"></span>
          </button>
          
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
