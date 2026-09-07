"""add_termo_na_portaria

Revision ID: b5c6d7e8f9a0
Revises: d3e4f5a6b7c8
Create Date: 2026-08-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'b5c6d7e8f9a0'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.add_column(
            sa.Column('termo_na_portaria', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute("ALTER TABLE condominios ALTER COLUMN termo_na_portaria DROP DEFAULT")


def downgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.drop_column('termo_na_portaria')
