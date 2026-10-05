import { NavLink, Outlet } from 'react-router-dom'
import { signOut, type Session } from './auth'

export default function App({ session }: { session: Session }) {
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
          {session.mode === 'oidc' && (
            <>
              <span className="small muted">{session.name}</span>
              <button onClick={() => void signOut()}>Sign out</button>
            </>
          )}
        </nav>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </>
  )
}
