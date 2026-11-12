import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from './AuthContext'

/** 未登录时跳转登录页，并记住原目标路径。 */
export default function RequireAuth() {
  const { token } = useAuth()
  const location = useLocation()

  if (token === null) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    )
  }
  return <Outlet />
}
