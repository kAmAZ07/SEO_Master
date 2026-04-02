import { Link, useLocation } from 'react-router-dom'
import {
  FileText,
  FolderKanban,
  KeyRound,
  LayoutDashboard,
  Link2,
  Search,
  Settings,
} from 'lucide-react'

const navigation = [
  { name: 'Dashboard', href: '/app', Icon: LayoutDashboard },
  { name: 'Projects', href: '/app/projects', Icon: FolderKanban },
  { name: 'Site Audit', href: '/app/audit', Icon: Search },
  { name: 'Keywords', href: '/app/keywords', Icon: KeyRound },
  { name: 'Content', href: '/app/content', Icon: FileText },
  { name: 'Backlinks', href: '/app/backlinks', Icon: Link2 },
  { name: 'Settings', href: '/app/settings', Icon: Settings },
]

const Sidebar = () => {
  const location = useLocation()

  return (
    <aside className="fixed left-0 top-16 h-[calc(100vh-4rem)] w-64 overflow-y-auto border-r border-gray-200 bg-white">
      <nav className="space-y-1 p-4">
        {navigation.map((item) => {
          const isActive = item.href === '/app'
            ? location.pathname === '/app'
            : location.pathname.startsWith(item.href)

          return (
            <Link
              key={item.name}
              to={item.href}
              className={`flex items-center rounded-lg px-4 py-3 text-sm font-medium transition-colors ${
                isActive ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'
              }`}
            >
              <item.Icon className="mr-3 h-4 w-4" />
              {item.name}
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}

export default Sidebar
