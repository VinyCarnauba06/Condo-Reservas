"""add_regimento_pdf_data_blob

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Branch Labels: None
Depends On: None

Store regimento PDF as binary blob in DB so it survives Railway ephemeral filesystem resets.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.add_column(sa.Column('regimento_pdf_data', sa.LargeBinary(), nullable=True))


def downgrade():
    with op.batch_alter_table('condominios') as batch_op:
        batch_op.drop_column('regimento_pdf_data')
