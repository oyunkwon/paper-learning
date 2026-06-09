import { BookOpenIcon, CheckIcon, ExternalLinkIcon, LockIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn, trackProgress } from "@/lib/utils"
import type { Chapter, ChapterStatus, Source, Track } from "@/types"

interface CurriculumBarProps {
  track: Track
  sources: Source[]
  viewingId: string | null
  onSelectChapter: (chapterId: string) => void
  // prereq only: toggle a chapter as already-known.
  onToggleKnown?: (chapterId: string, known: boolean) => void
}

// Track-aware progress column. For the prereq track, chapters are grouped by
// course (with a textbook label) and each is toggleable as "already known".
// For landscape/trends, chapters show the grounded sources they draw from.
export function CurriculumBar({
  track,
  sources,
  viewingId,
  onSelectChapter,
  onToggleKnown,
}: CurriculumBarProps) {
  const { done, total } = trackProgress(track)
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const sourceById = new Map(sources.map((s) => [s.id, s]))

  return (
    <div className="flex h-full flex-col">
      <header className="space-y-3 border-b px-4 py-4">
        <div>
          <h2 className="text-sm leading-snug font-semibold">{track.title}</h2>
          {track.summary && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {track.summary}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Progress value={pct} className="h-1.5" />
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{track.kind === "dependency" ? "선수지식 진행률" : "진행률"}</span>
            <span className="tabular-nums">
              {done} / {total}
            </span>
          </div>
        </div>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        {track.groups && track.groups.length > 0 ? (
          <div className="flex flex-col gap-4 p-3">
            {track.groups.map((group) => (
              <div key={group.id}>
                <div className="mb-1.5 px-1">
                  <h3 className="text-xs font-semibold text-foreground">
                    {group.title}
                  </h3>
                  {group.textbook && (
                    <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                      <BookOpenIcon className="size-3 shrink-0" />
                      <span className="truncate" title={group.textbook}>
                        {group.textbook}
                      </span>
                    </p>
                  )}
                </div>
                <ol className="flex flex-col gap-1.5">
                  {group.chapterIds.map((cid) => {
                    const ch = track.chapters.find((c) => c.id === cid)
                    if (!ch) return null
                    return (
                      <ChapterItem
                        key={ch.id}
                        chapter={ch}
                        sourceById={sourceById}
                        viewing={ch.id === viewingId}
                        onSelect={onSelectChapter}
                        onToggleKnown={onToggleKnown}
                      />
                    )
                  })}
                </ol>
              </div>
            ))}
          </div>
        ) : (
          <ol className="flex flex-col gap-1.5 p-3">
            {track.chapters.map((ch) => (
              <ChapterItem
                key={ch.id}
                chapter={ch}
                sourceById={sourceById}
                viewing={ch.id === viewingId}
                onSelect={onSelectChapter}
                onToggleKnown={onToggleKnown}
              />
            ))}
          </ol>
        )}
      </ScrollArea>
    </div>
  )
}

function ChapterItem({
  chapter,
  sourceById,
  viewing,
  onSelect,
  onToggleKnown,
}: {
  chapter: Chapter
  sourceById: Map<string, Source>
  viewing: boolean
  onSelect: (chapterId: string) => void
  onToggleKnown?: (chapterId: string, known: boolean) => void
}) {
  const done = chapter.status === "done"
  const locked = chapter.status === "locked"
  const known = Boolean(chapter.known)
  const showConcepts = (viewing || done) && chapter.concepts.length > 0
  const chapterSources = chapter.sourceIds
    .map((id) => sourceById.get(id))
    .filter((s): s is Source => Boolean(s))

  return (
    <li
      className={cn(
        "rounded-lg border transition-colors",
        viewing ? "border-primary/40 bg-accent/60" : "border-transparent",
        known && "opacity-60",
      )}
    >
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onSelect(chapter.id)}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-3 py-2.5 text-left",
            "hover:bg-accent/40",
            locked && "opacity-60",
          )}
        >
          <ChapterStatusIcon status={chapter.status} known={known} />
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm",
              viewing && "font-medium",
              done && !viewing && "text-muted-foreground",
              known && "line-through",
            )}
            title={chapter.title}
          >
            {chapter.title}
          </span>
          <StatusBadge status={chapter.status} known={known} />
        </button>

        {/* prereq: "already know this" toggle */}
        {onToggleKnown && (
          <button
            type="button"
            onClick={() => onToggleKnown(chapter.id, !known)}
            title={known ? "다시 학습하기" : "이미 알아요"}
            className={cn(
              "mr-2 shrink-0 rounded px-1.5 py-0.5 text-[11px] transition-colors",
              known
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:bg-accent",
            )}
          >
            {known ? "안다 ✓" : "안다"}
          </button>
        )}
      </div>

      {showConcepts && (
        <ol className="px-3 pb-2.5 pl-[42px]">
          {chapter.concepts.map((concept, ci) => (
            <ConceptStep
              key={ci}
              label={concept}
              done={Boolean(chapter.conceptsDone[ci])}
              isLast={ci === chapter.concepts.length - 1}
            />
          ))}
        </ol>
      )}

      {viewing && chapterSources.length > 0 && (
        <div className="px-3 pb-2.5 pl-[42px]">
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">
            참고 소스
          </p>
          <ul className="flex flex-col gap-1">
            {chapterSources.map((s) => (
              <li key={s.id}>
                <a
                  href={s.url ?? "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-start gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  <ExternalLinkIcon className="mt-0.5 size-3 shrink-0" />
                  <span className="min-w-0">
                    <span className="line-clamp-2">{s.title}</span>
                    {s.year && (
                      <span className="ml-1 tabular-nums opacity-70">
                        ({s.year})
                      </span>
                    )}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}

function ConceptStep({
  label,
  done,
  isLast,
}: {
  label: string
  done: boolean
  isLast: boolean
}) {
  return (
    <li className="flex gap-2.5">
      <div className="flex flex-col items-center">
        {done ? (
          <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <CheckIcon className="size-2.5" strokeWidth={3} />
          </span>
        ) : (
          <span className="size-4 shrink-0 rounded-full border border-muted-foreground/40 bg-background" />
        )}
        {!isLast && <div className={cn("w-px flex-1", done ? "bg-primary" : "bg-border")} />}
      </div>
      <span
        className={cn(
          "min-w-0 flex-1 text-sm leading-relaxed",
          isLast ? "pb-0.5" : "pb-3",
          done ? "text-muted-foreground line-through" : "text-foreground",
        )}
      >
        {label}
      </span>
    </li>
  )
}

function ChapterStatusIcon({
  status,
  known,
}: {
  status: ChapterStatus
  known: boolean
}) {
  const base = "flex size-5 shrink-0 items-center justify-center rounded-full"
  if (status === "done" || known) {
    return (
      <span className={cn(base, "bg-primary text-primary-foreground")}>
        <CheckIcon className="size-3" strokeWidth={3} />
      </span>
    )
  }
  if (status === "active") {
    return (
      <span className={cn(base, "border border-primary text-primary ring-2 ring-primary/15")} />
    )
  }
  return (
    <span className={cn(base, "bg-muted text-muted-foreground")}>
      <LockIcon className="size-3" />
    </span>
  )
}

const STATUS_LABEL: Record<ChapterStatus, string> = {
  done: "완료",
  active: "진행중",
  locked: "잠김",
}

function StatusBadge({ status, known }: { status: ChapterStatus; known: boolean }) {
  if (known) return null
  if (status === "locked") return null
  return (
    <Badge variant={status === "done" ? "secondary" : "default"} className="shrink-0">
      {STATUS_LABEL[status]}
    </Badge>
  )
}
