import { cn } from "@/lib/utils"
import { trackProgress } from "@/lib/utils"
import type { Curriculum, TrackId } from "@/types"

interface TrackNavProps {
  curriculum: Curriculum
  activeTrack: TrackId
  onSelectTrack: (trackId: TrackId) => void
}

// Top tab bar to switch between the four tracks. Each tab shows the track's
// completion (done/total), where a prereq chapter marked `known` counts as done.
export function TrackNav({ curriculum, activeTrack, onSelectTrack }: TrackNavProps) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b px-2 py-1.5">
      {curriculum.tracks.map((track) => {
        const { done, total } = trackProgress(track)
        const active = track.id === activeTrack
        return (
          <button
            key={track.id}
            type="button"
            onClick={() => onSelectTrack(track.id)}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent",
            )}
            title={track.summary || track.title}
          >
            <span className="font-medium">{track.title}</span>
            <span
              className={cn(
                "rounded px-1 text-[11px] tabular-nums",
                active ? "bg-primary-foreground/20" : "bg-muted",
              )}
            >
              {done}/{total}
            </span>
          </button>
        )
      })}
    </div>
  )
}
