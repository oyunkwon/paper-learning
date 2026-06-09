// REST client for the paper-learning backend. All paths relative; Vite proxies
// /api in dev, the backend serves the SPA in prod. Implements the contracts in
// docs/WORKSTREAMS.md (C2/C3/C4 + activate/pass).
//
// Mock mode: when VITE_MOCK=1 (or no backend reachable in dev), calls are served
// from src/lib/mock.ts so the UI can be developed without the backend (W4 is
// allowed to run ahead of W1/W3). See shouldMock().

import type {
  CreateSessionResponse,
  Curriculum,
  SessionDetail,
  SessionSummary,
  TrackId,
} from "@/types"
import * as mock from "@/lib/mock"

// Mock toggle: env flag at build, or ?mock=1 at runtime for quick demos.
export function shouldMock(): boolean {
  if (import.meta.env.VITE_MOCK === "1") return true
  if (typeof window !== "undefined") {
    return new URLSearchParams(window.location.search).has("mock")
  }
  return false
}

const MOCK = shouldMock()

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (body?.error?.message) return body.error.message as string
  } catch {
    // ignore
  }
  return `요청 실패 (${res.status})`
}

// ----- auth ----------------------------------------------------------------

export interface CurrentUser {
  id: string
  email: string
  name: string | null
  picture: string | null
}

export interface AuthStatus {
  google: boolean
  devBypass: boolean
}

// Resolve the current user, or null if not authenticated (401). In mock mode a
// fake local user is returned so the gate passes without a backend.
export async function getMe(): Promise<CurrentUser | null> {
  if (MOCK) {
    return { id: "mock-user", email: "mock@localhost", name: "Mock User", picture: null }
  }
  const res = await fetch("/api/auth/me")
  if (res.status === 401) return null
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function getAuthStatus(): Promise<AuthStatus> {
  if (MOCK) return { google: false, devBypass: true }
  const res = await fetch("/api/health")
  if (!res.ok) throw new Error(await readError(res))
  const body = await res.json()
  return body.auth ?? { google: false, devBypass: false }
}

export async function logout(): Promise<void> {
  if (MOCK) return
  await fetch("/api/auth/logout", { method: "POST" })
}

export function loginUrl(): string {
  return "/api/auth/login"
}

export async function createSession(file: File): Promise<CreateSessionResponse> {
  if (MOCK) return mock.createSession(file)
  const form = new FormData()
  form.append("file", file)
  const res = await fetch("/api/sessions", { method: "POST", body: form })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function listSessions(): Promise<SessionSummary[]> {
  if (MOCK) return mock.listSessions()
  const res = await fetch("/api/sessions")
  if (!res.ok) throw new Error(await readError(res))
  const body = await res.json()
  return body.sessions ?? []
}

export async function getSession(id: string): Promise<SessionDetail> {
  if (MOCK) return mock.getSession(id)
  const res = await fetch(`/api/sessions/${id}`)
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function deleteSession(id: string): Promise<void> {
  if (MOCK) return mock.deleteSession(id)
  const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" })
  if (!res.ok) throw new Error(await readError(res))
}

export async function renameSession(
  id: string,
  title: string,
): Promise<SessionSummary> {
  if (MOCK) return mock.renameSession(id, title)
  const res = await fetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export function materialUrl(id: string): string {
  return `/api/sessions/${id}/material`
}

export function chatUrl(id: string): string {
  return `/api/sessions/${id}/chat`
}

// C3: toggle a prereq chapter as already-known. Returns the updated curriculum.
export async function setKnown(
  id: string,
  trackId: TrackId,
  chapterId: string,
  known: boolean,
): Promise<Curriculum> {
  if (MOCK) return mock.setKnown(id, trackId, chapterId, known)
  const res = await fetch(`/api/sessions/${id}/known`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trackId, chapterId, known }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

// Unlock + activate a chapter within a track. Returns updated curriculum.
export async function activateChapter(
  id: string,
  trackId: TrackId,
  chapterId: string,
): Promise<Curriculum> {
  if (MOCK) return mock.activateChapter(id, trackId, chapterId)
  const res = await fetch(`/api/sessions/${id}/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trackId, chapterId }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

// Skip a chapter's current concept. Returns updated curriculum + the passed
// index and whether the chapter is now done.
export async function passConcept(
  id: string,
  trackId: TrackId,
  chapterId: string,
): Promise<{ curriculum: Curriculum; passedIndex: number; chapterDone: boolean }> {
  if (MOCK) return mock.passConcept(id, trackId, chapterId)
  const res = await fetch(`/api/sessions/${id}/pass`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trackId, chapterId }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}
