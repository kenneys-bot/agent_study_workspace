import { Navigate, Route, Routes } from 'react-router-dom'

import RequireAuth from './auth/RequireAuth'
import Layout from './components/Layout'
import ConsolePage from './pages/ConsolePage'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
import ReviewDetailPage from './pages/ReviewDetailPage'
import ReviewsPage from './pages/ReviewsPage'
import SystemPage from './pages/SystemPage'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/reviews" element={<ReviewsPage />} />
          <Route path="/reviews/:reviewId" element={<ReviewDetailPage />} />
          <Route path="/system" element={<SystemPage />} />
        </Route>
      </Route>
      <Route path="/" element={<Navigate to="/system" replace />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
