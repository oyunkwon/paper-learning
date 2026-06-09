import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

import type { Track } from "@/types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Track completion: a prereq chapter marked `known` counts as done.
export function trackProgress(track: Track): { done: number; total: number } {
  const done = track.chapters.filter(
    (c) => c.status === "done" || c.known,
  ).length
  return { done, total: track.chapters.length }
}

// Compact Korean relative time for the session sidebar (e.g. "방금 전", "3분 전").
// Accepts epoch seconds (backend `created_at`) or an ISO string.
export function relativeTime(at: number | string): string {
  const then =
    typeof at === "number" ? at * 1000 : new Date(at).getTime()
  if (Number.isNaN(then)) return ""
  const diff = Date.now() - then
  const sec = Math.round(diff / 1000)
  if (sec < 60) return "방금 전"
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}분 전`
  const hour = Math.round(min / 60)
  if (hour < 24) return `${hour}시간 전`
  const day = Math.round(hour / 24)
  if (day < 7) return `${day}일 전`
  return new Date(then).toLocaleDateString("ko-KR", {
    month: "short",
    day: "numeric",
  })
}
