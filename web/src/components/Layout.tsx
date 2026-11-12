import { NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/** 最小工作台布局：品牌、导航与退出登录。 */
export default function Layout() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  function handleSignOut() {
    signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            银
          </span>
          银行坐席辅助 · 本地工作台
        </div>
        <nav className="topnav" aria-label="主导航">
          <NavLink
            to="/system"
            className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          >
            系统版本
          </NavLink>
        </nav>
        <button type="button" className="button ghost" onClick={handleSignOut}>
          退出登录
        </button>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  )
}
