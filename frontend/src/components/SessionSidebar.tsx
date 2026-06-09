import { FileIcon, FileTextIcon, InboxIcon, LogOutIcon, PlusIcon, Trash2Icon } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
} from "@/components/ui/sidebar"
import { cn, relativeTime } from "@/lib/utils"
import { logout, type CurrentUser } from "@/lib/api"
import type { SessionSummary } from "@/types"

interface SessionSidebarProps {
  sessions: SessionSummary[]
  loading: boolean
  selectedId: string | null
  user: CurrentUser
  onSelect: (session: SessionSummary) => void
  onDelete: (session: SessionSummary) => void
  onNew: () => void
}

// Collapsible list of past learning sessions. Click a row to open it; the
// per-row action deletes the session server-side.
export function SessionSidebar({
  sessions,
  loading,
  selectedId,
  user,
  onSelect,
  onDelete,
  onNew,
}: SessionSidebarProps) {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={onNew} tooltip="새 논문 학습">
              <PlusIcon />
              <span>새 논문 학습</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>학습 기록</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {loading && sessions.length === 0 ? (
                <SkeletonList />
              ) : sessions.length === 0 ? (
                <EmptyState />
              ) : (
                sessions.map((s) => (
                  <SessionRow
                    key={s.id}
                    session={s}
                    active={s.id === selectedId}
                    onSelect={onSelect}
                    onDelete={onDelete}
                  />
                ))
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              tooltip={user.email}
              className="h-auto py-2"
              onClick={async () => {
                await logout()
                window.location.reload()
              }}
            >
              {user.picture ? (
                <img
                  src={user.picture}
                  alt=""
                  className="size-5 shrink-0 rounded-full"
                  referrerPolicy="no-referrer"
                />
              ) : (
                <LogOutIcon />
              )}
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="truncate text-sm">{user.name || user.email}</span>
                <span className="truncate text-[11px] text-muted-foreground">
                  로그아웃
                </span>
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}

interface SessionRowProps {
  session: SessionSummary
  active: boolean
  onSelect: (session: SessionSummary) => void
  onDelete: (session: SessionSummary) => void
}

function SessionRow({ session, active, onSelect, onDelete }: SessionRowProps) {
  const Icon = session.kind === "pdf" ? FileIcon : FileTextIcon
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={active}
        onClick={() => onSelect(session)}
        tooltip={session.title}
        className="h-auto py-2"
      >
        <Icon className="shrink-0" />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-sm">{session.title}</span>
          <span className="truncate text-[11px] text-muted-foreground">
            {relativeTime(session.createdAt)}
          </span>
        </span>
      </SidebarMenuButton>
      <SidebarMenuAction
        showOnHover
        aria-label="삭제"
        title="삭제"
        onClick={(e) => {
          e.stopPropagation()
          onDelete(session)
        }}
      >
        <Trash2Icon />
      </SidebarMenuAction>
    </SidebarMenuItem>
  )
}

function SkeletonList() {
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <SidebarMenuItem key={i}>
          <SidebarMenuSkeleton showIcon />
        </SidebarMenuItem>
      ))}
    </>
  )
}

function EmptyState() {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 px-2 py-8 text-center text-muted-foreground",
        "group-data-[collapsible=icon]:hidden",
      )}
    >
      <InboxIcon className="size-6" />
      <p className="text-xs">아직 학습 기록이 없습니다.</p>
    </div>
  )
}
