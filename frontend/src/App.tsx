import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { LoginGate } from "@/components/LoginGate"
import { SessionSidebar } from "@/components/SessionSidebar"
import { SessionView } from "@/components/SessionView"
import { UploadZone } from "@/components/UploadZone"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import {
  deleteSession as deleteSessionApi,
  listSessions,
  type CurrentUser,
} from "@/lib/api"
import type {
  Curriculum,
  CreateSessionResponse,
  SessionSummary,
} from "@/types"

function App() {
  return <LoginGate>{(user) => <Workspace user={user} />}</LoginGate>
}

function Workspace({ user }: { user: CurrentUser }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // Curriculum from the create response, so a freshly-made session opens
  // without a refetch (and so the auto-kickoff fires for the first chapter).
  const [preloaded, setPreloaded] = useState<{
    id: string
    curriculum: Curriculum
  } | null>(null)

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions())
    } catch (err) {
      toast.error("기록 불러오기 실패", {
        description: err instanceof Error ? err.message : "알 수 없는 오류",
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    listSessions()
      .then((list) => {
        if (active) setSessions(list)
      })
      .catch((err: unknown) => {
        toast.error("기록 불러오기 실패", {
          description: err instanceof Error ? err.message : "알 수 없는 오류",
        })
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const selected = sessions.find((s) => s.id === selectedId) ?? null

  const onCreated = useCallback((res: CreateSessionResponse) => {
    const summary: SessionSummary = {
      id: res.id,
      title: res.title,
      filename: res.filename,
      kind: res.kind,
      createdAt: res.createdAt,
      status: res.status,
      planningError: res.planningError,
      progress: res.progress,
    }
    setSessions((prev) => [summary, ...prev.filter((s) => s.id !== summary.id)])
    if (res.status === "ready" && res.curriculum) {
      setPreloaded({ id: res.id, curriculum: res.curriculum })
    } else {
      setPreloaded(null)
    }
    setSelectedId(summary.id)
  }, [])

  const onSelect = useCallback((s: SessionSummary) => {
    setPreloaded(null)
    setSelectedId(s.id)
  }, [])

  const onNew = useCallback(() => {
    setPreloaded(null)
    setSelectedId(null)
  }, [])

  const onDelete = useCallback(
    async (s: SessionSummary) => {
      setSessions((prev) => prev.filter((x) => x.id !== s.id))
      setSelectedId((cur) => (cur === s.id ? null : cur))
      try {
        await deleteSessionApi(s.id)
      } catch (err) {
        toast.error("삭제 실패", {
          description: err instanceof Error ? err.message : "알 수 없는 오류",
        })
        void refresh()
      }
    },
    [refresh],
  )

  const onRenamed = useCallback((id: string, title: string) => {
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)))
  }, [])

  return (
    <TooltipProvider delayDuration={300}>
      <SidebarProvider>
        <SessionSidebar
          sessions={sessions}
          loading={loading}
          selectedId={selectedId}
          user={user}
          onSelect={onSelect}
          onDelete={onDelete}
          onNew={onNew}
        />
        <SidebarInset className="min-h-svh">
          {selected ? (
            <SessionView
              key={selected.id}
              session={selected}
              onRenamed={onRenamed}
              preloaded={
                preloaded && preloaded.id === selected.id
                  ? { curriculum: preloaded.curriculum }
                  : undefined
              }
            />
          ) : (
            <div className="flex h-svh flex-col">
              <div className="flex items-center gap-2 border-b px-3 py-2">
                <SidebarTrigger />
                <span className="text-sm font-medium">새 논문 학습</span>
              </div>
              <div className="min-h-0 flex-1">
                <UploadZone onCreated={onCreated} />
              </div>
            </div>
          )}
        </SidebarInset>
        <Toaster position="bottom-right" />
      </SidebarProvider>
    </TooltipProvider>
  )
}

export default App
