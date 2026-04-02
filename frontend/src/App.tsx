import { Navigate, RouterProvider, createBrowserRouter, useParams } from 'react-router-dom'
import { Provider } from 'react-redux'
import { store } from './store/store'
import Layout from './components/Layout'
import PrivateRoute from './components/PrivateRoute'
import Home from './pages/Home'
import Dashboard from './pages/Dashboard'
import Login from './pages/auth/Login'
import Register from './pages/auth/Register'
import Projects from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import Audit from './pages/Audit'
import KeywordResearch from './pages/KeywordResearch'
import ContentOptimization from './pages/ContentOptimization'
import Backlinks from './pages/Backlinks'
import Settings from './pages/Settings'
import NotFound from './pages/NotFound'
import { useAppSelector } from './store/hooks'

const AuthAwareRedirect = ({ authenticatedTo }: { authenticatedTo: string }) => {
  const { isAuthenticated } = useAppSelector((state) => state.auth)
  return <Navigate to={isAuthenticated ? authenticatedTo : '/'} replace />
}

const LegacyProjectRedirect = () => {
  const { id } = useParams<{ id: string }>()
  const { isAuthenticated } = useAppSelector((state) => state.auth)

  if (!isAuthenticated) {
    return <Navigate to="/" replace />
  }

  return <Navigate to={id ? `/app/projects/${id}` : '/app/projects'} replace />
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <Home />,
  },
  {
    path: '/audit',
    element: <Audit />,
  },
  {
    path: '/login',
    element: <Login />,
  },
  {
    path: '/register',
    element: <Register />,
  },
  {
    path: '/app',
    element: (
      <PrivateRoute>
        <Layout />
      </PrivateRoute>
    ),
    children: [
      {
        index: true,
        element: <Dashboard />,
      },
      {
        path: 'projects',
        element: <Projects />,
      },
      {
        path: 'projects/:id',
        element: <ProjectDetail />,
      },
      {
        path: 'audit',
        element: <Audit />,
      },
      {
        path: 'keywords',
        element: <KeywordResearch />,
      },
      {
        path: 'content',
        element: <ContentOptimization />,
      },
      {
        path: 'backlinks',
        element: <Backlinks />,
      },
      {
        path: 'settings',
        element: <Settings />,
      },
    ],
  },
  {
    path: '/dashboard',
    element: <AuthAwareRedirect authenticatedTo="/app" />,
  },
  {
    path: '/projects',
    element: <AuthAwareRedirect authenticatedTo="/app/projects" />,
  },
  {
    path: '/projects/:id',
    element: <LegacyProjectRedirect />,
  },
  {
    path: '/keywords',
    element: <AuthAwareRedirect authenticatedTo="/app/keywords" />,
  },
  {
    path: '/content',
    element: <AuthAwareRedirect authenticatedTo="/app/content" />,
  },
  {
    path: '/backlinks',
    element: <AuthAwareRedirect authenticatedTo="/app/backlinks" />,
  },
  {
    path: '/settings',
    element: <AuthAwareRedirect authenticatedTo="/app/settings" />,
  },
  {
    path: '*',
    element: <NotFound />,
  },
])

function App() {
  return (
    <Provider store={store}>
      <RouterProvider router={router} />
    </Provider>
  )
}

export default App
