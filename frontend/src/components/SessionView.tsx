import { useCallback, useEffect, useRef, useState } from "react"
import { CheckIcon, Loader2Icon, PanelLeftCloseIcon, PanelLeftOpenIcon, PencilIcon, XIcon } from "lucide-react"
import { toast } from "sonner"

import { ChatPane } from "@/components/ChatPane"
import { CurriculumBar } from "@/components/CurriculumBar"
import { TrackNav } from "@/components/TrackNav"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { getSession, renameSession, activateChapter, setKnown } from "@/lib/api"
import type {
  ChatMessage,
  Curriculum,
  SessionDetail,
  SessionSummary,
  Threads,
  Track,
  TrackId,
} from "@/types"

interface SessionViewProps {
  session: SessionSummary
  preloaded?: { curriculum: Curriculum }
  onRenamed?: (id: string, title: string) => void
}

type LoadState =
  | { status: "loading" }
  | { status: "planning"; done: number; total: number }
  | { status: "ready"; detail: SessionDetail }
  | { status: "error"; message: string }

const DEFAULT_TRACK: TrackId = "paper"

// Multi-track learning view: [track nav] over [curriculum | chapter chat].
// Track state selects which curriculum column + chapter set is shown; chat
// threads are keyed by (track, chapter) per the C4 contract.
export function SessionView({ session, preloaded, onRenamed }: SessionViewProps) {
  const [load, setLoad] = useState<LoadState>(() =>
    preloaded
      ? {
          status: "ready",
          detail: { ...session, curriculum: preloaded.curriculum, threads: {} },
        }
      : { status: "loading" },
  )
  const [curriculum, setCurriculum] = useState<Curriculum | null>(
    preloaded?.curriculum ?? null,
  )
  const [threads, setThreads] = useState<Threads>({})
  const [activeTrack, setActiveTrack] = useState<TrackId>(
    () => preloaded?.curriculum?.tracks[0]?.id ?? DEFAULT_TRACK,
  )
  // Which chapter is shown, per track (preserved when switching tracks).
  const [viewingByTrack, setViewingByTrack] = useState<Record<string, string>>(
    () => initialViewing(preloaded?.curriculum ?? null),
  )
  const [showCurriculum, setShowCurriculum] = useState(true)
  const [advancing, setAdvancing] = useState(false)

  useEffect(() => {
    if (preloaded) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const applyReady = (detail: SessionDetail) => {
      setLoad({ status: "ready", detail })
      setCurriculum(detail.curriculum)
      setThreads(detail.threads ?? {})
      if (detail.curriculum) {
        setActiveTrack(detail.curriculum.tracks[0]?.id ?? DEFAULT_TRACK)
        setViewingByTrack(initialViewing(detail.curriculum))
      }
    }

    const poll = async () => {
      try {
        const detail = await getSession(session.id)
        if (!active) return
        if (detail.status === "planning") {
          const p = detail.progress ?? { done: 0, total: 0 }
          setLoad({ status: "planning", done: p.done, total: p.total })
          timer = setTimeout(poll, 1500)
        } else if (detail.status === "error") {
          setLoad({
            status: "error",
            message: detail.planningError || "커리큘럼 생성에 실패했어요.",
          })
        } else {
          applyReady(detail)
        }
      } catch (err: unknown) {
        if (active) {
          setLoad({
            status: "error",
            message: err instanceof Error ? err.message : "세션을 불러오지 못했습니다.",
          })
        }
      }
    }

    void poll()
    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [session.id, preloaded])

  const track: Track | null =
    curriculum?.tracks.find((t) => t.id === activeTrack) ?? null
  const viewingId = track ? viewingByTrack[track.id] ?? track.chapters[0]?.id ?? null : null
  const viewingChapter = track?.chapters.find((c) => c.id === viewingId) ?? null

  const onMastered = useCallback(
    (chapterId: string) => {
      setCurriculum((prev) =>
        prev ? markChapterDone(prev, activeTrack, chapterId) : prev,
      )
    },
    [activeTrack],
  )

  const onConceptDone = useCallback(
    (chapterId: string, index: number) => {
      setCurriculum((prev) =>
        prev ? tickConcept(prev, activeTrack, chapterId, index) : prev,
      )
    },
    [activeTrack],
  )

  const onConceptPassed = useCallback((updated: Curriculum) => {
    setCurriculum(updated)
  }, [])

  const onNext = useCallback(async () => {
    const t = curriculum?.tracks.find((x) => x.id === activeTrack)
    if (!curriculum || !t) return
    const current = viewingByTrack[activeTrack] ?? t.chapters[0]?.id ?? null
    if (!current) return
    const nextId = nextChapterId(t, current)
    if (!nextId) return
    setAdvancing(true)
    try {
      const updated = await activateChapter(session.id, activeTrack, nextId)
      setCurriculum(updated)
      setViewingByTrack((prev) => ({ ...prev, [activeTrack]: nextId }))
    } catch (err) {
      toast.error("다음 챕터로 이동 실패", {
        description: err instanceof Error ? err.message : "알 수 없는 오류",
      })
    } finally {
      setAdvancing(false)
    }
  }, [curriculum, viewingByTrack, session.id, activeTrack])

  const onSelectChapter = useCallback(
    async (chapterId: string) => {
      const t = curriculum?.tracks.find((x) => x.id === activeTrack)
      if (!curriculum || !t) return
      const ch = t.chapters.find((c) => c.id === chapterId)
      if (!ch) return
      if (ch.status === "locked") {
        try {
          const updated = await activateChapter(session.id, activeTrack, chapterId)
          setCurriculum(updated)
        } catch (err) {
          toast.error("챕터 열기 실패", {
            description: err instanceof Error ? err.message : "알 수 없는 오류",
          })
          return
        }
      }
      setViewingByTrack((prev) => ({ ...prev, [activeTrack]: chapterId }))
    },
    [curriculum, session.id, activeTrack],
  )

  const onToggleKnown = useCallback(
    async (chapterId: string, known: boolean) => {
      try {
        const updated = await setKnown(session.id, activeTrack, chapterId, known)
        setCurriculum(updated)
      } catch (err) {
        toast.error("상태 변경 실패", {
          description: err instanceof Error ? err.message : "알 수 없는 오류",
        })
      }
    },
    [session.id, activeTrack],
  )

  const onThreadChange = useCallback(
    (chapterId: string, messages: ChatMessage[]) => {
      setThreads((prev) => ({
        ...prev,
        [activeTrack]: { ...(prev[activeTrack] ?? {}), [chapterId]: messages },
      }))
    },
    [activeTrack],
  )

  const trackThreads = threads[activeTrack] ?? {}

  return (
    <div className="flex h-svh flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2 sm:gap-3 sm:px-4">
        <SidebarTrigger />
        <Separator orientation="vertical" className="!h-4" />
        <Button
          variant="ghost"
          size="sm"
          className="hidden gap-1.5 md:inline-flex"
          onClick={() => setShowCurriculum((v) => !v)}
        >
          {showCurriculum ? (
            <PanelLeftCloseIcon className="size-4" />
          ) : (
            <PanelLeftOpenIcon className="size-4" />
          )}
          커리큘럼
        </Button>
        <EditableTitle
          sessionId={session.id}
          title={session.title}
          onRenamed={onRenamed}
        />
      </div>

      {load.status === "loading" ? (
        <Centered>
          <Loader2Icon className="size-8 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">세션을 불러오는 중...</p>
        </Centered>
      ) : load.status === "planning" ? (
        <PlanningView done={load.done} total={load.total} />
      ) : load.status === "error" ? (
        <Centered>
          <p className="text-sm text-muted-foreground">{load.message}</p>
        </Centered>
      ) : curriculum ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <TrackNav
            curriculum={curriculum}
            activeTrack={activeTrack}
            onSelectTrack={setActiveTrack}
          />
          <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
            {showCurriculum && track && (
              <>
                <ResizablePanel defaultSize="30%" minSize="18%" maxSize="44%" className="min-h-0">
                  <CurriculumBar
                    track={track}
                    sources={curriculum.sources}
                    viewingId={viewingId}
                    onSelectChapter={onSelectChapter}
                    onToggleKnown={
                      track.kind === "dependency" ? onToggleKnown : undefined
                    }
                  />
                </ResizablePanel>
                <ResizableHandle withHandle />
              </>
            )}
            <ResizablePanel defaultSize="70%" minSize="40%" className="min-h-0">
              {track && viewingChapter ? (
                <ChatPane
                  key={`${session.id}:${activeTrack}:${viewingChapter.id}`}
                  sessionId={session.id}
                  trackId={activeTrack}
                  chapterId={viewingChapter.id}
                  chapterTitle={viewingChapter.title}
                  initialMessages={trackThreads[viewingChapter.id] ?? []}
                  mastered={viewingChapter.status === "done"}
                  hasNext={nextChapterId(track, viewingChapter.id) !== null}
                  advancing={advancing}
                  onMastered={onMastered}
                  onConceptDone={onConceptDone}
                  onConceptPassed={onConceptPassed}
                  onNext={onNext}
                  onThreadChange={onThreadChange}
                />
              ) : (
                <Centered>
                  <p className="text-sm text-muted-foreground">
                    이 트랙에는 챕터가 없습니다.
                  </p>
                </Centered>
              )}
            </ResizablePanel>
          </ResizablePanelGroup>
        </div>
      ) : (
        <Centered>
          <p className="text-sm text-muted-foreground">
            커리큘럼을 불러오지 못했습니다.
          </p>
        </Centered>
      )}
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3">
      {children}
    </div>
  )
}

function PlanningView({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 5
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 p-8">
      <Loader2Icon className="size-8 animate-spin text-primary" />
      <div className="w-full max-w-sm space-y-2 text-center">
        <p className="text-base font-medium">논문을 분석하고 있어요</p>
        <p className="text-sm text-muted-foreground">
          논문을 읽고, 선행연구와 후속 연구를 조사해 학습 트랙을 설계하는 중이에요.
        </p>
        <Progress value={pct} className="h-2" />
        <p className="text-xs text-muted-foreground tabular-nums">
          {total > 0 ? `${pct}% (${done}/${total} 단계)` : "준비 중…"}
        </p>
      </div>
    </div>
  )
}

function EditableTitle({
  sessionId,
  title,
  onRenamed,
}: {
  sessionId: string
  title: string
  onRenamed?: (id: string, title: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(title)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const start = useCallback(() => {
    setValue(title)
    setEditing(true)
    requestAnimationFrame(() => inputRef.current?.select())
  }, [title])

  const cancel = useCallback(() => {
    setEditing(false)
    setValue(title)
  }, [title])

  const save = useCallback(async () => {
    const next = value.trim()
    if (!next || next === title) {
      cancel()
      return
    }
    setSaving(true)
    try {
      const updated = await renameSession(sessionId, next)
      onRenamed?.(sessionId, updated.title)
      setEditing(false)
    } catch (err) {
      toast.error("이름 변경 실패", {
        description: err instanceof Error ? err.message : "알 수 없는 오류",
      })
    } finally {
      setSaving(false)
    }
  }, [value, title, sessionId, onRenamed, cancel])

  if (!editing) {
    return (
      <button
        type="button"
        onClick={start}
        title="제목 수정"
        className="group flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-medium hover:bg-accent"
      >
        <span className="truncate">{title}</span>
        <PencilIcon className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </button>
    )
  }

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
      <Input
        ref={inputRef}
        value={value}
        disabled={saving}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            void save()
          } else if (e.key === "Escape") {
            e.preventDefault()
            cancel()
          }
        }}
        className="h-8 max-w-md text-sm"
      />
      <Button size="icon" variant="ghost" className="size-8" disabled={saving} onClick={() => void save()}>
        {saving ? <Loader2Icon className="size-4 animate-spin" /> : <CheckIcon className="size-4" />}
      </Button>
      <Button size="icon" variant="ghost" className="size-8" disabled={saving} onClick={cancel}>
        <XIcon className="size-4" />
      </Button>
    </div>
  )
}

// --- pure curriculum reducers (mirror backend mutations, track-scoped) ------

function initialViewing(curriculum: Curriculum | null): Record<string, string> {
  const out: Record<string, string> = {}
  if (!curriculum) return out
  for (const t of curriculum.tracks) {
    const active = t.chapters.find((c) => c.status === "active") ?? t.chapters[0]
    if (active) out[t.id] = active.id
  }
  return out
}

function mapTrack(
  curriculum: Curriculum,
  trackId: TrackId,
  fn: (track: Track) => Track,
): Curriculum {
  return {
    ...curriculum,
    tracks: curriculum.tracks.map((t) => (t.id === trackId ? fn(t) : t)),
  }
}

function markChapterDone(
  curriculum: Curriculum,
  trackId: TrackId,
  chapterId: string,
): Curriculum {
  return mapTrack(curriculum, trackId, (track) => ({
    ...track,
    chapters: track.chapters.map((c) =>
      c.id === chapterId
        ? { ...c, status: "done" as const, conceptsDone: c.concepts.map(() => true) }
        : c,
    ),
  }))
}

function tickConcept(
  curriculum: Curriculum,
  trackId: TrackId,
  chapterId: string,
  index: number,
): Curriculum {
  return mapTrack(curriculum, trackId, (track) => ({
    ...track,
    chapters: track.chapters.map((c) => {
      if (c.id !== chapterId) return c
      const conceptsDone = [...c.conceptsDone]
      if (index >= 0 && index < conceptsDone.length) conceptsDone[index] = true
      return { ...c, conceptsDone }
    }),
  }))
}

function nextChapterId(track: Track, chapterId: string): string | null {
  const idx = track.chapters.findIndex((c) => c.id === chapterId)
  if (idx === -1 || idx + 1 >= track.chapters.length) return null
  return track.chapters[idx + 1].id
}
