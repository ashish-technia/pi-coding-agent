import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import 'diff2html/bundles/css/diff2html.min.css'
import './index.css'
import App from './App'
import { initAuth } from './auth'
import HomePage from './pages/HomePage'
import RunPage from './pages/RunPage'
import SettingsPage from './pages/SettingsPage'

const root = createRoot(document.getElementById('root')!)

// Nothing renders until we know who is using the page: with sign-in on, an anonymous
// visitor is sent to the issuer before any API call is made.
initAuth()
  .then((session) => {
    root.render(
      <StrictMode>
        <BrowserRouter>
          <Routes>
            <Route element={<App session={session} />}>
              <Route index element={<HomePage />} />
              <Route path="runs/:issueKey" element={<RunPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </StrictMode>,
    )
  })
  .catch((error: unknown) => {
    root.render(
      <main className="page">
        <div className="card">
          <h2>Sign-in failed</h2>
          <p className="small">{error instanceof Error ? error.message : String(error)}</p>
          <button onClick={() => window.location.assign('/')}>Try again</button>
        </div>
      </main>,
    )
  })
