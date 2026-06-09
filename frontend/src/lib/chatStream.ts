// Reads an NDJSON stream from POST /chat and dispatches structured frames (C2).
// No EventSource (that's GET-only); we use fetch + a ReadableStream reader.
//
// In mock mode we synthesize a fake token stream so the chat UI works without a
// backend.

import { chatUrl, shouldMock } from "@/lib/api"
import * as mock from "@/lib/mock"
import type { ChatFrame, TrackId } from "@/types"

export interface ChatStreamHandlers {
  onToken?: (text: string) => void
  onConceptDone?: (chapterId: string, index: number) => void
  onMastered?: (chapterId: string) => void
  onDone?: (content: string) => void
  onError?: (message: string) => void
}

export interface ChatStreamArgs {
  trackId: TrackId
  chapterId: string
  message?: string
  kickoff?: boolean
  pass?: boolean
}

// Streams one tutor turn. Resolves when the stream ends.
export async function streamChat(
  sessionId: string,
  args: ChatStreamArgs,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  if (shouldMock()) {
    await mock.streamChat(sessionId, args, handlers, signal)
    return
  }

  let res: Response
  try {
    res = await fetch(chatUrl(sessionId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
      signal,
    })
  } catch (err) {
    if ((err as Error).name === "AbortError") return
    handlers.onError?.(err instanceof Error ? err.message : "연결 실패")
    return
  }

  if (!res.ok || !res.body) {
    let msg = `요청 실패 (${res.status})`
    try {
      const body = await res.json()
      if (body?.error?.message) msg = body.error.message
    } catch {
      // ignore
    }
    handlers.onError?.(msg)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trim()
        buffer = buffer.slice(nl + 1)
        if (line) dispatch(line, handlers)
      }
    }
    const tail = buffer.trim()
    if (tail) dispatch(tail, handlers)
  } catch (err) {
    if ((err as Error).name === "AbortError") return
    handlers.onError?.(err instanceof Error ? err.message : "스트림 오류")
  }
}

function dispatch(line: string, handlers: ChatStreamHandlers): void {
  let frame: ChatFrame
  try {
    frame = JSON.parse(line)
  } catch {
    return
  }
  switch (frame.type) {
    case "token":
      handlers.onToken?.(frame.text)
      break
    case "concept_done":
      handlers.onConceptDone?.(frame.chapterId, frame.index)
      break
    case "mastered":
      handlers.onMastered?.(frame.chapterId)
      break
    case "done":
      handlers.onDone?.(frame.content)
      break
    case "error":
      handlers.onError?.(frame.message)
      break
  }
}
