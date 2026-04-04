import { Link } from 'react-router-dom';
import Button from '../components/ui/Button';

const NotFound = () => {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="text-9xl font-bold text-blue-600">404</h1>
        <p className="text-2xl font-semibold text-gray-800 mt-4">Страница не найдена</p>
        <p className="text-gray-600 mt-2 mb-8">
          Запрашиваемая страница не существует или была удалена
        </p>
        <Link to="/">
          <Button>Вернуться на главную</Button>
        </Link>
      </div>
    </div>
  );
};

export default NotFound;
