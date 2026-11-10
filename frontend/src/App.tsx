import { useEffect, useRef } from 'react';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';
import { Provider } from 'react-redux';
import { store } from './store/store';
import { useAppDispatch, useAppSelector } from './store/hooks';
import { loadUser } from './store/slices/authSlice';
import { PENDING_PROJECT_SAVE_KEY } from './api/session';
import Dashboard from './pages/Dashboard';
import Login from './pages/auth/Login';
import Register from './pages/auth/Register';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';
import ProjectAuditReport from './pages/ProjectAuditReport';
import Audit from './pages/Audit';
import KeywordResearch from './pages/KeywordResearch';
import ContentOptimization from './pages/ContentOptimization';
import Backlinks from './pages/Backlinks';
import HITL from './pages/HITL';
import Settings from './pages/Settings';
import NotFound from './pages/NotFound';
import Layout from './components/Layout';
import PrivateRoute from './components/PrivateRoute';
import Loader from './components/ui/Loader';

// При старте с сохранённым токеном профиль запрашивается заново: токен сам по себе входом не считается.
const SessionBootstrap = () => {
  const dispatch = useAppDispatch();
  const sessionStatus = useAppSelector((state) => state.auth.sessionStatus);
  const started = useRef(false);

  useEffect(() => {
    if (sessionStatus === 'restoring' && !started.current) {
      started.current = true;
      void dispatch(loadUser());
    }
  }, [dispatch, sessionStatus]);

  return null;
};

const HomeRoute = () => {
  const sessionStatus = useAppSelector((state) => state.auth.sessionStatus);

  if (sessionStatus === 'restoring') {
    return <Loader />;
  }

  if (sessionStatus === 'authenticated') {
    try {
      const pendingSave = JSON.parse(localStorage.getItem(PENDING_PROJECT_SAVE_KEY) || 'null');
      if (pendingSave?.uid) {
        return <Navigate to={`/audit/results/${pendingSave.uid}?saveProject=1`} replace />;
      }
    } catch {
      // Повреждённая запись в localStorage: считаем, что отложенного сохранения нет.
    }

    return <Navigate to="/dashboard" replace />;
  }

  return <Audit />;
};

const router = createBrowserRouter([
  {
    path: '/login',
    element: <Login />,
  },
  {
    path: '/register',
    element: <Register />,
  },
  {
    path: '/audit',
    element: <Audit />,
  },
  {
    path: '/audit/results/:uid',
    element: <Audit />,
  },
  {
    path: '/',
    element: <HomeRoute />
  },
  {
    path: '/dashboard',
    element: <PrivateRoute><Layout /></PrivateRoute>,
    children: [
      {
        index: true,
        element: <Dashboard />
      },
      {
        path: 'projects',
        element: <Projects />
      },
      {
        path: 'projects/:id',
        element: <ProjectDetail />
      },
      {
        path: 'projects/:projectId/audits/:uid',
        element: <ProjectAuditReport />
      },
      {
        path: 'keywords',
        element: <KeywordResearch />
      },
      {
        path: 'content',
        element: <ContentOptimization />
      },
      {
        path: 'backlinks',
        element: <Backlinks />
      },
      {
        path: 'hitl',
        element: <HITL />
      },
      {
        path: 'settings',
        element: <Settings />
      },
  ]},
  {
    path: '*',
    element: <NotFound />,
  },
]);

function App() {
  return (
    <Provider store={store}>
      <SessionBootstrap />
      <RouterProvider router={router} />
    </Provider>
  );
}

export default App;
