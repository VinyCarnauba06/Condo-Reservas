"""add_observacao_to_creditos

Revision ID: 9f8e7d6c5b4a
Revises: f8e9d0c1b2a3
Create Date: 2026-06-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision      = '9f8e7d6c5b4a'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column('creditos', sa.Column('observacao', sa.String(200), nullable=True))


def downgrade():
    op.drop_column('creditos', 'observacao')
