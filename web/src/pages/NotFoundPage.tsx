import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return (
    <main className="panel">
      <h1>页面不存在</h1>
      <p className="muted">请检查地址，或返回系统版本页。</p>
      <Link className="button" to="/system">
        返回系统版本
      </Link>
    </main>
  )
}
