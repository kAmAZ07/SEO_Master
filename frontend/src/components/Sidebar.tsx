import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, FolderOpen, Search, CheckSquare,
  Key, FileText, Link2, Settings as SettingsIcon
} from 'lucide-react';

const navigation = [
  { name: 'Панель управления', href: '/dashboard',           icon: LayoutDashboard },
  { name: 'Проекты',           href: '/dashboard/projects',  icon: FolderOpen },
  { name: 'Аудит сайта',       href: '/audit',               icon: Search },
  { name: 'HITL-согласования', href: '/dashboard/hitl',      icon: CheckSquare },
  { name: 'Ключевые слова',    href: '/dashboard/keywords',  icon: Key },
  { name: 'Контент',           href: '/dashboard/content',   icon: FileText },
  { name: 'Обратные ссылки',   href: '/dashboard/backlinks', icon: Link2 },
  { name: 'Настройки',         href: '/dashboard/settings',  icon: SettingsIcon },
];

const Sidebar = () => {
  const location = useLocation();

  return (
    <aside className="fixed left-0 top-16 h-[calc(100vh-4rem)] w-64 bg-white border-r border-gray-200 overflow-y-auto">
      <nav className="p-4 space-y-1">
        {navigation.map((item) => {
          const isActive = location.pathname === item.href;
          return (
            <Link
              key={item.name}
              to={item.href}
              className={`flex items-center px-4 py-3 text-sm font-medium rounded-lg transition-colors ${
                isActive
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-700 hover:bg-gray-50'
              }`}
            >
              <span className="mr-3 text-lg">{item.icon}</span>
              {item.name}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
};

export default Sidebar;
