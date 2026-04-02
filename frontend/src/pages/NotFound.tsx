import { Link } from 'react-router-dom'
import Button from '../components/ui/Button'

const NotFound = () => {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="text-9xl font-bold text-blue-600">404</h1>
        <p className="mt-4 text-2xl font-semibold text-gray-800">Page not found</p>
        <p className="mb-8 mt-2 text-gray-600">The page you requested does not exist or was moved.</p>
        <Link to="/">
          <Button>Go back home</Button>
        </Link>
      </div>
    </div>
  )
}

export default NotFound
