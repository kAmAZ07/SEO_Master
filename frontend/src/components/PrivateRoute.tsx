import { Navigate, Outlet } from 'react-router-dom';
import { useAppSelector } from '../store/hooks';
import Loader from './ui/Loader';

interface PrivateRouteProps {
  children?: React.ReactNode;
}

const PrivateRoute = ({ children }: PrivateRouteProps) => {
  const { isAuthenticated, loading } = useAppSelector((state) => state.auth);

  if (loading) {
    return <Loader />;
  }

  return isAuthenticated ? (children ?? <Outlet/>) : <Navigate to="/login" replace />;
};

export default PrivateRoute;
