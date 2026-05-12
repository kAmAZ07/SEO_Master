import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';
import { Provider } from 'react-redux';
import { store } from './store/store';
import { useAppSelector } from './store/hooks';
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
import Layout from './components/Layout';
import PrivateRoute from './components/PrivateRoute';

const HomeRoute = () => {
  const { isAuthenticated, token } = useAppSelector((state) => state.auth);

  if (isAuthenticated || token) {
    try {
      const pendingSave = JSON.parse(localStorage.getItem('seoMaster.pendingProjectSave') || 'null');
      if (pendingSave?.uid) {
        return <Navigate to={`/audit/results/${pendingSave.uid}?saveProject=1`} replace />;
      }
    } catch {
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
]);

function App() {
  return (
    <Provider store={store}>
      <RouterProvider router={router} />
    </Provider>
  );
}

export default App;
