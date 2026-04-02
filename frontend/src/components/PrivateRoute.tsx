import { Navigate, useLocation } from 'react-router-dom';
import { useAppSelector } from '../store/hooks';
import Loader from './ui/Loader';

interface PrivateRouteProps {
  children: React.ReactNode;
}

const PrivateRoute = ({ children }: PrivateRouteProps) => {
  const location = useLocation();
  const { isAuthenticated, loading } = useAppSelector((state) => state.auth);

  if (loading) {
    return <Loader />;
  }

  return isAuthenticated ? <>{children}</> : <Navigate to="/" replace state={{ from: location.pathname, authRequired: true }} />;
};

export default PrivateRoute;
