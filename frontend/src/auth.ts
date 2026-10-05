// Sign-in for the UI. The server says how (`GET /api/auth/config`): with `oidc` the browser signs
// in at the issuer with the authorization-code flow and PKCE, and every API call carries the
// access token; with `none` (a server bound to this machine) there is nothing to do.
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

type AuthConfig = { mode: 'none' } | { mode: 'oidc'; issuer: string; client_id: string; scope: string }

export type Session = { mode: 'none' | 'oidc'; name: string }

let manager: UserManager | null = null
let user: User | null = null

function here(): string {
  return window.location.pathname + window.location.search
}

/** Resolve who is using the page before anything renders. Redirects to the issuer when nobody is signed in. */
export async function initAuth(): Promise<Session> {
  const res = await fetch('/api/auth/config')
  if (!res.ok) throw new Error(`Could not read the sign-in configuration (HTTP ${res.status}).`)
  const cfg = (await res.json()) as AuthConfig
  if (cfg.mode !== 'oidc') return { mode: 'none', name: '' }

  manager = new UserManager({
    authority: cfg.issuer,
    client_id: cfg.client_id,
    redirect_uri: `${window.location.origin}/`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: 'code',
    scope: cfg.scope,
    automaticSilentRenew: true,
    // Per tab, gone when the tab closes; tokens are never written to localStorage.
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  })
  manager.events.addUserLoaded((renewed) => {
    user = renewed
  })

  const params = new URLSearchParams(window.location.search)
  if (params.has('code') && params.has('state')) {
    // Back from the issuer: trade the code for tokens, then return to the page that was asked for.
    user = await manager.signinRedirectCallback()
    window.history.replaceState({}, '', typeof user.state === 'string' ? user.state : '/')
  } else {
    user = await manager.getUser()
  }

  if (!user || user.expired) {
    await manager.signinRedirect({ state: here() })
    return new Promise<Session>(() => {}) // the browser is leaving for the issuer
  }
  const profile = user.profile
  return { mode: 'oidc', name: profile.name ?? profile.preferred_username ?? profile.email ?? '' }
}

export function accessToken(): string | null {
  return user && !user.expired ? user.access_token : null
}

/** The API answered 401: the token expired or was revoked. Sign in again and come back here. */
export async function signInAgain(): Promise<void> {
  await manager?.signinRedirect({ state: here() })
}

export async function signOut(): Promise<void> {
  await manager?.signoutRedirect()
}
