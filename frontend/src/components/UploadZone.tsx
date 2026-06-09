import { useCallback, useRef, useState } from "react"
import { BookOpenIcon, Loader2Icon, UploadIcon } from "lucide-react"
import { toast } from "sonner"

import { Card } from "@/components/ui/card"
import { createSession } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { CreateSessionResponse } from "@/types"

interface UploadZoneProps {
  onCreated: (session: CreateSessionResponse) => void
}

const ACCEPT = ".pdf,.md"

function isAccepted(file: File): boolean {
  return /\.(pdf|md)$/i.test(file.name)
}

export function UploadZone({ onCreated }: UploadZoneProps) {
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(
    async (file: File) => {
      if (!isAccepted(file)) {
        toast.error("지원하지 않는 형식", {
          description: "PDF 또는 Markdown 파일만 가능합니다.",
        })
        return
      }
      setBusy(true)
      try {
        const session = await createSession(file)
        onCreated(session)
      } catch (err) {
        toast.error("업로드 실패", {
          description: err instanceof Error ? err.message : "알 수 없는 오류",
        })
        setBusy(false)
      }
    },
    [onCreated],
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const file = e.dataTransfer.files?.[0]
      if (file) void handleFile(file)
    },
    [handleFile],
  )

  const onSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) void handleFile(file)
      e.target.value = ""
    },
    [handleFile],
  )

  return (
    <div className="flex h-full items-center justify-center p-8">
      <Card
        role="button"
        tabIndex={0}
        aria-disabled={busy}
        onClick={() => !busy && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) inputRef.current?.click()
        }}
        onDragOver={(e) => {
          e.preventDefault()
          if (!busy) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex h-72 w-full max-w-xl cursor-pointer flex-col items-center justify-center gap-3 border-2 border-dashed text-center transition-colors",
          dragging ? "border-primary bg-muted/50" : "text-muted-foreground",
          busy && "pointer-events-none cursor-default",
        )}
      >
        {busy ? (
          <>
            <Loader2Icon className="size-8 animate-spin text-primary" />
            <div className="space-y-1">
              <p className="text-base font-medium text-foreground">업로드 중...</p>
              <p className="text-sm text-muted-foreground">
                자료를 올리고 있어요. 곧 커리큘럼 설계가 시작돼요.
              </p>
            </div>
          </>
        ) : (
          <>
            {dragging ? (
              <BookOpenIcon className="size-8 text-primary" />
            ) : (
              <UploadIcon className="size-8 text-muted-foreground" />
            )}
            <div className="space-y-1">
              <p className="text-base font-medium text-foreground">
                학습할 PDF 또는 Markdown 파일을 드롭하세요
              </p>
              <p className="text-sm text-muted-foreground">
                클릭해서 파일을 선택할 수도 있습니다 (.pdf, .md)
              </p>
            </div>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={onSelect}
        />
      </Card>
    </div>
  )
}
