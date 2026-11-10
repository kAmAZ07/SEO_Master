import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { loadUser } from '../store/slices/authSlice';
import Button from './ui/Button';
import Loader from './ui/Loader';

interface PrivateRouteProps {
  children?: React.ReactNode;
}

const PrivateRoute = ({ children }: PrivateRouteProps) => {
  const dispatch = useAppDispatch();
  const location = useLocation();
  const { sessionStatus, error } = useAppSelector((state) => state.auth);

  // Наличие токена в localStorage не означает вход: пока сервер не подтвердил профиль, доступ закрыт.
  if (sessionStatus === 'restoring') {
    return <Loader />;
  }

  if (sessionStatus === 'restore_failed') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
        <div role="alert" className="max-w-md rounded-lg border border-red-200 bg-white p-6 text-center">
          <h1 className="text-lg font-semibold text-gray-900">Не удалось восстановить сессию</h1>
          <p className="mt-2 text-sm text-gray-600">{error || 'Сервер временно недоступен.'}</p>
          <Button type="button" className="mt-4" onClick={() => void dispatch(loadUser())}>
            Повторить
          </Button>
        </div>
      </div>
    );
  }

  if (sessionStatus === 'authenticated') {
    return <>{children ?? <Outlet />}</>;
  }

  const redirect = encodeURIComponent(`${location.pathname}${location.search}`);
  return <Navigate to={`/login?redirect=${redirect}`} replace />;
};

export default PrivateRoute;
