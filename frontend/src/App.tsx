import { NavLink, Outlet } from 'react-router-dom'

export default function App() {
  return (
    <>
      <header className="topbar">
        <div className="brand">
          Pi Jira Agent<span>Jira issue to reviewed pull request</span>
        </div>
        <nav>
          <NavLink to="/" end>
            Runs
          </NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </>
  )
}
