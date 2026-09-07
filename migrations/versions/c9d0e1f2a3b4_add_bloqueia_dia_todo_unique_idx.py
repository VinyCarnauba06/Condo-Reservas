"""add_bloqueia_dia_todo_unique_idx

Revision ID: c9d0e1f2a3b4
Revises: a4b5c6d7e8f9
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision      = 'c9d0e1f2a3b4'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on    = None


def upgrade():
    # [FEATURE] índice único parcial contra double-booking de salão
    # dia_inteiro sob concorrência (duas pessoas do setor batendo a mesma
    # data quase ao mesmo tempo). Postgres não permite predicado de índice
    # parcial referenciar outra tabela, então denormalizamos o tipo do
    # salão no momento da reserva (bloqueia_dia_todo), gravado em
    # nova_reserva()/editar_reserva() em app/routes/reservas.py.
    # Validado antes de aplicar: 0 colisões hoje em produção pra
    # (salao_id, data_festa) em salões dia_inteiro, e 0 casos de salão
    # horario_fixo com mais de uma reserva ativa no mesmo dia — por isso o
    # índice fica restrito a bloqueia_dia_todo = true, sem afetar
    # horario_fixo (que legitimamente permite múltiplos horários por dia).
    op.add_column(
        'reservas',
        sa.Column('bloqueia_dia_todo', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.execute("""
        UPDATE reservas r
        SET bloqueia_dia_todo = true
        FROM saloes s
        WHERE s.id = r.salao_id AND s.tipo = 'dia_inteiro'
    """)
    op.alter_column('reservas', 'bloqueia_dia_todo', server_default=None)

    # CREATE INDEX CONCURRENTLY não pode rodar dentro de uma transaction
    op.execute('COMMIT')
    op.execute(
        'CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS '
        'ix_reserva_dia_inteiro_unico ON reservas(salao_id, data_festa) '
        "WHERE status != 'cancelado' AND bloqueia_dia_todo = true"
    )


def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_reserva_dia_inteiro_unico')
    op.drop_column('reservas', 'bloqueia_dia_todo')
