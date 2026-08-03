"""Add routing taxonomy fields to drafts and skills.

- drafts.routing_category, drafts.feedback_reason_code
- skills.always_apply
- Normalize legacy skills.category free-text onto the closed RoutingCategory set
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017_routing_taxonomy"
down_revision: str | None = "016_embed_search_document"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_KNOWN: dict[str, str] = {
    "billing": "billing",
    "invoice": "billing",
    "payment": "billing",
    "scheduling": "scheduling",
    "schedule": "scheduling",
    "escalation": "escalation",
    "escalate": "escalation",
    "vendor": "vendor",
    "internal": "internal",
    "general": "general",
    "other": "general",
}


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column("routing_category", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "drafts",
        sa.Column("feedback_reason_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column(
            "always_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, category FROM skills")).fetchall()
    for skill_id, category in rows:
        if category is None or not str(category).strip():
            normalized = "general"
        else:
            key = str(category).strip().lower()
            normalized = _KNOWN.get(key, "general")
            for known, mapped in _KNOWN.items():
                if known in key or key in known:
                    normalized = mapped
                    break
        bind.execute(
            sa.text("UPDATE skills SET category = :cat WHERE id = :id"),
            {"cat": normalized, "id": skill_id},
        )


def downgrade() -> None:
    op.drop_column("skills", "always_apply")
    op.drop_column("drafts", "feedback_reason_code")
    op.drop_column("drafts", "routing_category")
