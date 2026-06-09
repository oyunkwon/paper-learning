// Shared frontend types. Mirrors the backend JSON shapes defined in
// docs/WORKSTREAMS.md (the interface SSOT). Multi-track curriculum.

export type SessionKind = "pdf" | "md"

export type ChapterStatus = "locked" | "active" | "done"

export type TrackId = "paper" | "prereq" | "landscape" | "trends"
export type TrackKind = "dependency" | "reading"

export interface Chapter {
  id: string
  title: string
  summary: string
  concepts: string[]
  conceptsDone: boolean[]
  status: ChapterStatus
  pageStart: number | null
  pageEnd: number | null
  // landscape/trends grounding: ids into Curriculum.sources.
  sourceIds: string[]
  // prereq only: learner toggled "I already know this".
  known?: boolean
}

// prereq track only: course-unit grouping over its chapters.
export interface TrackGroup {
  id: string
  title: string
  textbook: string | null
  chapterIds: string[]
}

export interface Track {
  id: TrackId
  kind: TrackKind
  title: string
  summary: string
  groups?: TrackGroup[] // prereq only
  chapters: Chapter[]
}

export interface PaperMeta {
  title: string
  authors: string[]
  arxivId: string | null
  doi: string | null
  year: number | null
  abstract: string | null
  venue: string | null
}

export type SourceType = "paper" | "survey" | "textbook"

export interface Source {
  id: string
  type: SourceType
  title: string
  authors: string[]
  url: string | null
  doi: string | null
  arxivId: string | null
  year: number | null
  venue: string | null
  retrievedFrom: string
}

export interface Curriculum {
  paper: PaperMeta
  tracks: Track[]
  graph?: { nodes: unknown[]; edges: unknown[] }
  sources: Source[]
}

export type SessionStatus = "planning" | "ready" | "error"

export interface SessionProgress {
  done: number
  total: number
}

export interface SessionSummary {
  id: string
  title: string
  filename: string
  kind: SessionKind
  createdAt: number
  status: SessionStatus
  planningError?: string | null
  progress?: SessionProgress
}

// POST /api/sessions response = summary + (curriculum null until planning done)
export interface CreateSessionResponse extends SessionSummary {
  curriculum: Curriculum | null
}

export interface ChatMessage {
  role: "user" | "assistant"
  content: string
  ts?: number
  // System-injected turns (chapter kickoff, concept pass), hidden from the UI.
  synthetic?: boolean
}

// C4: threads are 2-level nested — { trackId: { chapterId: ChatMessage[] } }.
export type Threads = Record<string, Record<string, ChatMessage[]>>

// GET /api/sessions/{id} response
export interface SessionDetail extends SessionSummary {
  curriculum: Curriculum | null
  threads: Threads
}

// NDJSON frames from POST /chat (C2)
export type ChatFrame =
  | { type: "token"; text: string }
  | { type: "concept_done"; chapterId: string; index: number }
  | { type: "mastered"; chapterId: string }
  | { type: "done"; content: string }
  | { type: "error"; message: string }
