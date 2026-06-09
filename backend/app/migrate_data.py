"""로컬 dev 데이터를 prod로 이전한다 (세션 + 스레드 + 커리큘럼 + 바이너리).

두 단계로 쓴다:

  1) export — 로컬 dev DB(구 스키마: user 없음)와 로컬 PDF/페이지를 하나의
     디렉터리 번들로 뽑는다. 스키마-agnostic raw SQL이라 구/신 스키마 모두 읽는다.

       uv run python -m app.migrate_data export --out /tmp/bundle

  2) import — 번들을 대상(prod) DB에 적재한다. 모든 세션을 ``--email``로 지정한
     유저 소유로 만든다(없으면 그 유저를 화이트리스트에 추가 후 생성). 바이너리는
     현재 STORAGE 설정(R2 or 로컬 캐시)으로 업로드된다.

       # prod에 한 번 Google 로그인해서 유저 행이 생긴 뒤 실행.
       DATABASE_URL=... R2_*=... \
       uv run python -m app.migrate_data import --in /tmp/bundle --email you@company.com

번들 레이아웃:
  bundle/sessions.json                 # [{id, title, filename, kind, page_count,
                                        #   curriculum, status, created_at}, ...]
  bundle/threads.json                  # [{session_id, track_id, chapter_id, messages}]
  bundle/binaries/<session_id>/material.<kind>
  bundle/binaries/<session_id>/pages/page-NNN.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import DEFAULT_DATA_DIR
from app.db import dispose_engine, session_scope


# --- export -----------------------------------------------------------------


async def _export(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "binaries").mkdir(exist_ok=True)

    async with session_scope() as db:
        # 세션: 구 스키마엔 user_id가 없을 수 있으니 SELECT *로 받고 dict로.
        rows = (await db.execute(text("SELECT * FROM sessions"))).mappings().all()
        sessions = [_jsonable(dict(r)) for r in rows]

        tids = (await db.execute(text("SELECT * FROM threads"))).mappings().all()
        threads = [_jsonable(dict(r)) for r in tids]

    (out / "sessions.json").write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "threads.json").write_text(
        json.dumps(threads, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 바이너리: 로컬 데이터 디렉터리에서 세션별로 복사.
    # 구 레이아웃: <DATA>/sessions/<id>/{material.kind, pages/}
    # 신 레이아웃: <DATA>/users/<uid>/sessions/<id>/{...}
    n_bin = 0
    for s in sessions:
        sid = str(s["id"])
        src = _find_local_session_dir(sid)
        if src is None:
            print(f"  ! 바이너리 없음: {sid[:8]} (건너뜀)")
            continue
        dst = out / "binaries" / sid
        _copytree(src, dst)
        n_bin += 1

    print(f"export 완료: 세션 {len(sessions)}, 스레드 {len(threads)}, 바이너리 {n_bin}")
    print(f"  -> {out}")


def _find_local_session_dir(session_id: str) -> Path | None:
    # 신 레이아웃 우선.
    for base in (DEFAULT_DATA_DIR / "users",):
        if base.exists():
            for udir in base.iterdir():
                cand = udir / "sessions" / session_id
                if cand.exists():
                    return cand
    # 구 레이아웃.
    cand = DEFAULT_DATA_DIR / "sessions" / session_id
    return cand if cand.exists() else None


# --- import -----------------------------------------------------------------


async def _import(src: Path, email: str) -> None:
    from app import storage as storage_mod
    from app.models import AllowedEmail, User

    sessions = json.loads((src / "sessions.json").read_text(encoding="utf-8"))
    threads = json.loads((src / "threads.json").read_text(encoding="utf-8"))

    email = email.lower()
    async with session_scope() as db:
        # 대상 유저 해소(없으면 생성 + 화이트리스트 등록).
        row = (
            await db.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).first()
        if row is None:
            uid = uuid.uuid4()
            db.add(User(id=uid, email=email, name=email.split("@")[0]))
            if await db.get(AllowedEmail, email) is None:
                db.add(AllowedEmail(email=email))
            print(f"  유저 생성: {email} ({uid})")
        else:
            uid = row[0]
            print(f"  기존 유저 사용: {email} ({uid})")

    # 세션 + 스레드 적재.
    for s in sessions:
        await _import_one_session(s, threads, uid)

    # 바이너리 업로드(현재 STORAGE 설정 = R2 or 로컬 캐시).
    n_bin = 0
    for s in sessions:
        sid = str(s["id"])
        bdir = src / "binaries" / sid
        if not bdir.exists():
            continue
        await _upload_binaries(storage_mod, str(uid), sid, s.get("kind", "pdf"), bdir)
        n_bin += 1

    print(f"import 완료: 세션 {len(sessions)}, 바이너리 {n_bin}, 유저 {email}")


async def _import_one_session(
    s: dict[str, Any], all_threads: list[dict[str, Any]], uid: uuid.UUID
) -> None:
    sid = uuid.UUID(str(s["id"]))
    curriculum = s.get("curriculum")
    if isinstance(curriculum, str):
        curriculum = json.loads(curriculum)
    async with session_scope() as db:
        exists = await db.execute(
            text("SELECT 1 FROM sessions WHERE id = :id"), {"id": str(sid)}
        )
        if exists.first() is not None:
            print(f"  세션 이미 존재, 건너뜀: {str(sid)[:8]}")
            return
        db.add(
            _SessionInsert(
                id=sid,
                user_id=uid,
                title=s.get("title", "논문"),
                filename=s.get("filename", "paper"),
                kind=s.get("kind", "pdf"),
                page_count=int(s.get("page_count") or 0),
                curriculum=curriculum,
                status=s.get("status", "ready"),
            )
        )
    # 스레드.
    mine = [t for t in all_threads if str(t.get("session_id")) == str(sid)]
    for t in mine:
        msgs = t.get("messages")
        if isinstance(msgs, str):
            msgs = json.loads(msgs)
        async with session_scope() as db:
            db.add(
                _ThreadInsert(
                    session_id=sid,
                    track_id=t.get("track_id", "paper"),
                    chapter_id=t.get("chapter_id", ""),
                    messages=msgs or [],
                )
            )


async def _upload_binaries(storage_mod, uid: str, sid: str, kind: str, bdir: Path) -> None:
    material = bdir / f"material.{kind}"
    if material.exists():
        key = storage_mod.material_key(uid, sid, kind)
        await storage_mod.storage.put_bytes(key, material.read_bytes())
    pages = bdir / "pages"
    if pages.exists():
        for p in sorted(pages.glob("page-*.jpg")):
            idx = int(p.stem.split("-")[1])
            key = storage_mod.page_key(uid, sid, idx)
            await storage_mod.storage.put_file(key, p)


# ORM-less insert 헬퍼: 모델 import 없이 행을 만든다(스키마 변동에 견고).
def _SessionInsert(**kw):
    from app.models import SessionRow

    return SessionRow(**kw)


def _ThreadInsert(**kw):
    from app.models import Thread

    return Thread(**kw)


# --- utils ------------------------------------------------------------------


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (datetime,)):
            out[k] = v.astimezone(timezone.utc).isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _copytree(src: Path, dst: Path) -> None:
    import shutil

    shutil.copytree(src, dst, dirs_exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="paper-learning 데이터 이전")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="로컬 DB+바이너리를 번들로 export")
    p_exp.add_argument("--out", required=True, type=Path)

    p_imp = sub.add_parser("import", help="번들을 대상 DB로 import")
    p_imp.add_argument("--in", dest="inp", required=True, type=Path)
    p_imp.add_argument("--email", required=True, help="세션을 소유할 유저 이메일")

    args = ap.parse_args()

    async def run() -> None:
        try:
            if args.cmd == "export":
                await _export(args.out)
            else:
                await _import(args.inp, args.email)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
