import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  getAuthStatus,
  getMe,
  loginUrl,
  type CurrentUser,
} from "@/lib/api"

type GateState =
  | { status: "loading" }
  | { status: "authed"; user: CurrentUser }
  | { status: "anon"; googleEnabled: boolean }

const AUTH_ERROR_COPY: Record<string, string> = {
  not_allowed: "접근 권한이 없는 계정이에요. 관리자에게 화이트리스트 등록을 요청하세요.",
  oauth: "구글 로그인에 실패했어요. 다시 시도해주세요.",
  email: "이메일 확인에 실패했어요. 다른 계정으로 시도해주세요.",
}

/**
 * Gates the app behind authentication. While the dev bypass is on (backend),
 * /api/auth/me returns the seeded user and we render children directly. In prod
 * (Google configured, no session) we show a login screen.
 */
export function LoginGate({
  children,
}: {
  children: (user: CurrentUser) => React.ReactNode
}) {
  const [state, setState] = useState<GateState>({ status: "loading" })

  useEffect(() => {
    let active = true
    ;(async () => {
      try {
        const user = await getMe()
        if (!active) return
        if (user) {
          setState({ status: "authed", user })
        } else {
          const auth = await getAuthStatus()
          if (active) setState({ status: "anon", googleEnabled: auth.google })
        }
      } catch {
        if (active) setState({ status: "anon", googleEnabled: false })
      }
    })()
    return () => {
      active = false
    }
  }, [])

  if (state.status === "loading") {
    return (
      <div className="flex h-svh items-center justify-center">
        <div className="text-sm text-muted-foreground">불러오는 중…</div>
      </div>
    )
  }

  if (state.status === "authed") {
    return <>{children(state.user)}</>
  }

  // Anonymous: show login screen.
  const params = new URLSearchParams(window.location.search)
  const errKey = params.get("auth_error")
  const errMsg = errKey ? AUTH_ERROR_COPY[errKey] ?? "로그인 중 문제가 발생했어요." : null

  return (
    <div className="flex h-svh flex-col items-center justify-center gap-6 px-6">
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-2xl font-semibold">논문 학습 튜터</h1>
        <p className="max-w-sm text-sm text-muted-foreground">
          논문을 올리면 선수지식·지형·트렌드·논문 자체를 트랙으로 만들어, 튜터와
          함께 한 개념씩 짚으며 공부해요.
        </p>
      </div>

      {errMsg && (
        <div className="max-w-sm rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2 text-center text-sm text-destructive">
          {errMsg}
        </div>
      )}

      {state.googleEnabled ? (
        <Button asChild size="lg">
          <a href={loginUrl()}>Google로 계속하기</a>
        </Button>
      ) : (
        <div className="max-w-sm text-center text-sm text-muted-foreground">
          로그인이 설정되지 않았어요. 백엔드에 Google OAuth를 구성하거나
          개발 모드(AUTH_DEV_BYPASS=1)로 실행하세요.
        </div>
      )}
    </div>
  )
}
