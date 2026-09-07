"""add_fiscal_proprio_seg_sab

[TESTE] Condomínio com fiscal próprio fixo (seg-sáb) — a vistoria/revistoria
só é da administradora quando o dia da ação cai num domingo (dia que o
fiscal próprio não trabalha). Ver Condominio.precisa_vistoria_em() em
app/models.py, único ponto que decide isso. Ver CHANGELOG_VISTORIA_MOBILE.md
(raiz do projeto) para rollback.

Revision ID: f1a2b3c4d5e6
Revises: e3f4a5b6c7d8
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'f1a2b3c4d5e6'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on    = None


def _insp():
    return sa.inspect(op.get_bind())


def _column_names(tabela):
    if tabela not in _insp().get_table_names():
        return set()
    return {c['name'] for c in _insp().get_columns(tabela)}


def upgrade():
    if 'fiscal_proprio_seg_sab' not in _column_names('condominios'):
        op.add_column(
            'condominios',
            sa.Column('fiscal_proprio_seg_sab', sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    if 'fiscal_proprio_seg_sab' in _column_names('condominios'):
        op.drop_column('condominios', 'fiscal_proprio_seg_sab')
