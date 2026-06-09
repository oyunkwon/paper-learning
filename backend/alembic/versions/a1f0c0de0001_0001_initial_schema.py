"""0001 initial schema (multi-user: users + allowed_emails + sessions + threads)

Revision ID: a1f0c0de0001
Revises:
Create Date: 2026-06-10 01:30:00.000000

"""
from typing import Sequence, Union

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1f0c0de0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 인증 전(또는 dev bypass) 단일 유저. models.DEFAULT_USER_ID와 동일해야 한다.
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_EMAIL = "local@localhost"

# Postgres에선 JSONB, 그 외(SQLite)에선 generic JSON.
_JSON = sa.JSON().with_variant(
    __import__("sqlalchemy.dialects.postgresql", fromlist=["JSONB"]).JSONB(),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("picture", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "allowed_emails",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column(
            "page_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("curriculum", _JSON, nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
        sa.Column("planning_error", sa.Text(), nullable=True),
        sa.Column(
            "progress_done", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "progress_total", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind in ('pdf','md')", name="ck_sessions_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_sessions_user_created", "sessions", ["user_id", "created_at"], unique=False
    )

    op.create_table(
        "threads",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=64), nullable=False),
        sa.Column(
            "messages",
            _JSON,
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "track_id", "chapter_id"),
    )

    # 시드: DEFAULT_USER (dev bypass / 로컬 단일 유저가 의존).
    users = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("email", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users,
        [
            {
                "id": DEFAULT_USER_ID,
                "email": DEFAULT_USER_EMAIL,
                "name": "Local User",
                "created_at": datetime.now(timezone.utc),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("threads")
    op.drop_index("ix_sessions_user_created", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("allowed_emails")
    op.drop_table("users")
