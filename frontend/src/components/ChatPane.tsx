import { memo, useCallback, useEffect, useRef, useState } from "react"
import { ArrowRightIcon, CheckCircle2Icon, Loader2Icon, SendIcon, SkipForwardIcon } from "lucide-react"
import { toast } from "sonner"

import { Markdown } from "@/components/Markdown"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { passConcept } from "@/lib/api"
import { streamChat } from "@/lib/chatStream"
import type { ChatMessage, Curriculum, TrackId } from "@/types"

interface ChatPaneProps {
  sessionId: string
  trackId: TrackId
  chapterId: string
  chapterTitle: string
  initialMessages: ChatMessage[]
  // True once this chapter is mastered (locally or from the server).
  mastered: boolean
  // Whether a following chapter exists to advance to.
  hasNext: boolean
  // True while the parent is activating the next chapter.
  advancing: boolean
  onMastered: (chapterId: string) => void
  onConceptDone: (chapterId: string, index: number) => void
  // Learner pressed "pass": skip the current concept. The pane calls the API,
  // then hands the updated curriculum up so the parent's state stays in sync.
  onConceptPassed: (curriculum: Curriculum) => void
  onNext: () => void
  // Lifts this chapter's thread up so it survives chapter switches.
  onThreadChange: (chapterId: string, messages: ChatMessage[]) => void
}

export function ChatPane({
  sessionId,
  trackId,
  chapterId,
  chapterTitle,
  initialMessages,
  mastered,
  hasNext,
  advancing,
  onMastered,
  onConceptDone,
  onConceptPassed,
  onNext,
  onThreadChange,
}: ChatPaneProps) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [streaming, setStreaming] = useState(false)
  const [passing, setPassing] = useState(false)
  // The in-progress assistant reply, rendered as a live bubble.
  const [pending, setPending] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const kickedRef = useRef(false)
  // rAF-coalesced streaming buffer: many tokens -> one render per frame.
  const accRef = useRef("")
  const rafRef = useRef<number | null>(null)

  const flushPending = useCallback(() => {
    rafRef.current = null
    setPending(accRef.current)
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  const scheduleFlush = useCallback(() => {
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(flushPending)
    }
  }, [flushPending])

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }, [])

  const runTurn = useCallback(
    async (args: { message?: string; kickoff?: boolean; pass?: boolean }) => {
      setStreaming(true)
      setPending("")
      accRef.current = ""
      const ctrl = new AbortController()
      abortRef.current = ctrl

      try {
        await streamChat(
          sessionId,
          { ...args, trackId, chapterId },
          {
            onToken: (text) => {
              accRef.current += text
              scheduleFlush()
            },
            onConceptDone,
            onMastered,
            onDone: (content) => {
              if (rafRef.current != null) {
                cancelAnimationFrame(rafRef.current)
                rafRef.current = null
              }
              setMessages((prev) => [...prev, { role: "assistant", content }])
              setPending(null)
              accRef.current = ""
              scrollToBottom()
            },
            onError: (message) => {
              toast.error("튜터 응답 오류", { description: message })
              setPending(null)
            },
          },
          ctrl.signal,
        )
      } catch (err) {
        // Never leave the composer stuck disabled if the stream blows up.
        toast.error("튜터 응답 오류", {
          description: err instanceof Error ? err.message : "알 수 없는 오류",
        })
        setPending(null)
      } finally {
        if (rafRef.current != null) {
          cancelAnimationFrame(rafRef.current)
          rafRef.current = null
        }
        setStreaming(false)
        abortRef.current = null
      }
    },
    [sessionId, chapterId, onConceptDone, onMastered, scheduleFlush, scrollToBottom, trackId],
  )

  // Auto-kickoff the first turn for a chapter whose thread is empty.
  // Deferred so the first setState happens outside the effect body.
  useEffect(() => {
    if (kickedRef.current) return
    kickedRef.current = true
    if (initialMessages.length > 0) return
    const id = setTimeout(() => void runTurn({ kickoff: true }), 0)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Tear down any in-flight stream on unmount / chapter switch.
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  // Lift completed-message changes up so the parent's thread cache stays fresh
  // (skips the in-progress `pending` bubble; only settled messages persist).
  useEffect(() => {
    onThreadChange(chapterId, messages)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages])

  const onSend = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || streaming) return
      setMessages((prev) => [...prev, { role: "user", content: trimmed }])
      scrollToBottom()
      void runTurn({ message: trimmed })
    },
    [streaming, runTurn, scrollToBottom],
  )

  // Skip the current concept: mark it done server-side, sync the curriculum up,
  // then trigger a turn so the tutor moves to the next concept.
  const onPass = useCallback(async () => {
    if (streaming || passing) return
    setPassing(true)
    try {
      const { curriculum } = await passConcept(sessionId, trackId, chapterId)
      onConceptPassed(curriculum)
      await runTurn({ pass: true })
    } catch (err) {
      toast.error("개념 건너뛰기 실패", {
        description: err instanceof Error ? err.message : "알 수 없는 오류",
      })
    } finally {
      setPassing(false)
    }
  }, [streaming, passing, sessionId, trackId, chapterId, onConceptPassed, runTurn])

  return (
    <div className="flex h-full flex-col">
      {/* Chapter header. */}
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        {mastered ? (
          <CheckCircle2Icon className="size-4 shrink-0 text-primary" />
        ) : (
          <span className="size-2 shrink-0 rounded-full bg-primary" />
        )}
        <span className="truncate text-sm font-medium" title={chapterTitle}>
          {chapterTitle}
        </span>
      </div>

      <ScrollArea className="min-h-0 flex-1" viewportRef={scrollRef}>
        <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4">
          {messages.length === 0 && pending === null && !streaming && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              튜터가 곧 학습을 시작합니다.
            </p>
          )}
          {messages.map((m, i) =>
            m.synthetic ? null : (
              <Bubble key={i} role={m.role} content={m.content} />
            ),
          )}
          {pending !== null && (
            <Bubble role="assistant" content={pending} streaming />
          )}

          {/* Pass: skip the current concept. Shown only when the tutor is idle
              and waiting on the learner (last visible turn is the assistant's),
              and the chapter isn't already mastered. */}
          {!streaming &&
            pending === null &&
            !mastered &&
            messages.some((m) => !m.synthetic) &&
            (() => {
              const visible = messages.filter((m) => !m.synthetic)
              const last = visible[visible.length - 1]
              return last?.role === "assistant"
            })() && (
              <div className="flex justify-center">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onPass}
                  disabled={passing}
                  className="gap-1.5 text-muted-foreground"
                >
                  {passing ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <SkipForwardIcon className="size-4" />
                  )}
                  이 개념 건너뛰기
                </Button>
              </div>
            )}

          {mastered && !streaming && (
            <div className="mx-auto my-2 flex w-full max-w-md flex-col items-center gap-3 rounded-xl border bg-accent/40 p-4 text-center">
              <CheckCircle2Icon className="size-6 text-primary" />
              <p className="text-sm font-medium">이 챕터를 완료했어요.</p>
              {hasNext ? (
                <Button onClick={onNext} disabled={advancing} className="gap-1.5">
                  {advancing ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <>
                      다음 챕터로
                      <ArrowRightIcon className="size-4" />
                    </>
                  )}
                </Button>
              ) : (
                <p className="text-xs text-muted-foreground">
                  모든 챕터를 마쳤어요. 수고했어요!
                </p>
              )}
            </div>
          )}
        </div>
      </ScrollArea>

      <Composer disabled={streaming} onSend={onSend} />
    </div>
  )
}

const Bubble = memo(function Bubble({
  role,
  content,
  streaming,
}: {
  role: "user" | "assistant"
  content: string
  streaming?: boolean
}) {
  const isUser = role === "user"
  return (
    <div
      className={cn("flex", isUser ? "justify-end" : "justify-start")}
      // Skip layout/paint for off-screen messages. KaTeX/highlight produce huge
      // DOM subtrees; once many messages accumulate, painting them all on every
      // scroll or keystroke is what makes the view janky. `content-visibility`
      // lets the browser skip rendering work for bubbles outside the viewport,
      // while `contain-intrinsic-size` reserves an estimated height so the
      // scrollbar doesn't jump. Not applied to the live streaming bubble, whose
      // height changes every frame. (No-op in browsers without support.)
      style={
        streaming
          ? undefined
          : { contentVisibility: "auto", containIntrinsicSize: "auto 120px" }
      }
    >
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-foreground",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{content}</p>
        ) : content === "" && streaming ? (
          <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
        ) : (
          <Markdown className="prose-sm">{content}</Markdown>
        )}
      </div>
    </div>
  )
})

// Composer owns its own input state so typing never re-renders the message
// list (each Bubble runs react-markdown + katex; re-rendering all of them on
// every keystroke is what caused the input lag as the transcript grew).
const Composer = memo(function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean
  onSend: (text: string) => void
}) {
  const [value, setValue] = useState("")

  const submit = useCallback(() => {
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue("")
  }, [value, disabled, onSend])

  return (
    <div className="border-t p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          rows={1}
          placeholder="답변을 입력하세요. (Shift+Enter 줄바꿈)"
          className={cn(
            "max-h-40 min-h-[2.75rem] flex-1 resize-none rounded-md border bg-background px-3 py-2.5 text-base sm:min-h-[2.5rem] sm:py-2 sm:text-sm",
            "outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          )}
        />
        <Button
          size="icon"
          onClick={submit}
          disabled={disabled || !value.trim()}
          className="size-11 shrink-0 sm:size-9"
        >
          {disabled ? <Loader2Icon className="animate-spin" /> : <SendIcon />}
        </Button>
      </div>
    </div>
  )
})
