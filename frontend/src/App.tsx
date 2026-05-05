import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { Provider } from 'react-redux';
import { store } from './store/store';
import Dashboard from './pages/Dashboard';
import Login from './pages/auth/Login';
import Register from './pages/auth/Register';
import Projects from './pages/Projects';
import ProjectDetail from './pages/ProjectDetail';
import Audit from './pages/Audit';
import KeywordResearch from './pages/KeywordResearch';
import ContentOptimization from './pages/ContentOptimization';
import Backlinks from './pages/Backlinks';
import HITL from './pages/HITL';
import Settings from './pages/Settings';
import PrivateRoute from './components/PrivateRoute';

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
    element: <Audit /> 
  },
  { 
    path: '/dashboard',
    element: <PrivateRoute />, 
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
