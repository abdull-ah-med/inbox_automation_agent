"""Add users.token_version for access-token invalidation on password change."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Must be <= 32 chars (alembic_version.version_num VARCHAR(32)).
revision: str = "009_users_token_version"
down_revision: str | None = "008_threads_mb_lma_idx"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "token_version" in columns:
        return

    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "token_version" not in columns:
        return
    op.drop_column("users", "token_version")
