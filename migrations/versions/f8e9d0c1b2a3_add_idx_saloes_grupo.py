"""add_idx_saloes_grupo

Revision ID: f8e9d0c1b2a3
Revises: c4e33d93992a
Create Date: 2026-05-24 00:00:00.000000

"""
from alembic import op


revision      = 'f8e9d0c1b2a3'
down_revision = 'c4e33d93992a'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_index('idx_saloes_grupo', 'saloes', ['grupo'])


def downgrade():
    op.drop_index('idx_saloes_grupo', table_name='saloes')
