"""add_encryption_fields_to_reservas

Revision ID: 6882bb1dc130
Revises:
Create Date: 2026-05-14 09:14:46.731755

"""
import os
import logging
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '6882bb1dc130'
down_revision = None
branch_labels = None
depends_on = None


def _get_cipher():
    from cryptography.fernet import Fernet
    key = os.getenv('ENCRYPTION_KEY')
    if not key:
        raise ValueError("ENCRYPTION_KEY não encontrada no .env")
    return Fernet(key.encode() if isinstance(key, str) else key)


def upgrade():
    conn = op.get_bind()

    # Passo 1: adicionar novos campos como nullable (para não quebrar rows existentes)
    with op.batch_alter_table('reservas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('_nome_solicitante_enc', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('_contato_enc', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('_apartamento_enc', sa.String(length=500), nullable=True))

    # Passo 2: criptografar dados existentes
    cipher = _get_cipher()
    rows = conn.execute(text(
        "SELECT id, nome_solicitante, contato, apartamento FROM reservas"
    )).fetchall()

    for row in rows:
        row_id, nome, contato, apartamento = row

        nome_enc = cipher.encrypt(nome.encode()).decode() if nome else ''
        contato_enc = cipher.encrypt(contato.encode()).decode() if contato else None
        apartamento_enc = cipher.encrypt(apartamento.encode()).decode() if apartamento else ''

        conn.execute(text(
            "UPDATE reservas SET _nome_solicitante_enc = :nome, _contato_enc = :contato, "
            "_apartamento_enc = :apt WHERE id = :id"
        ), {"nome": nome_enc, "contato": contato_enc, "apt": apartamento_enc, "id": row_id})

    logging.info(f"[migration] {len(rows)} reserva(s) criptografada(s).")

    # Passo 3: remover colunas antigas e índices (se existirem)
    bind = op.get_bind()
    insp = sa.inspect(bind)
    idx_names = {i['name'] for i in insp.get_indexes('reservas')}

    with op.batch_alter_table('reservas', schema=None) as batch_op:
        for idx in ['ix_reservas_data_festa', 'ix_reservas_salao_id', 'ix_reservas_status',
                    'ix_reserva_data_festa',  'ix_reserva_salao_id',  'ix_reserva_status']:
            if idx in idx_names:
                batch_op.drop_index(idx)
        batch_op.drop_column('contato')
        batch_op.drop_column('apartamento')
        batch_op.drop_column('nome_solicitante')


def downgrade():
    conn = op.get_bind()

    # Passo 1: readicionar colunas antigas como nullable
    with op.batch_alter_table('reservas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('nome_solicitante', sa.VARCHAR(length=200), nullable=True))
        batch_op.add_column(sa.Column('apartamento', sa.VARCHAR(length=20), nullable=True))
        batch_op.add_column(sa.Column('contato', sa.VARCHAR(length=100), nullable=True))

    # Passo 2: descriptografar dados de volta
    from cryptography.fernet import Fernet, InvalidToken
    cipher = _get_cipher()
    rows = conn.execute(text(
        "SELECT id, _nome_solicitante_enc, _contato_enc, _apartamento_enc FROM reservas"
    )).fetchall()

    for row in rows:
        row_id, nome_enc, contato_enc, apt_enc = row
        try:
            nome = cipher.decrypt(nome_enc.encode()).decode() if nome_enc else ''
        except (InvalidToken, Exception):
            nome = nome_enc or ''
        try:
            contato = cipher.decrypt(contato_enc.encode()).decode() if contato_enc else None
        except (InvalidToken, Exception):
            contato = contato_enc
        try:
            apt = cipher.decrypt(apt_enc.encode()).decode() if apt_enc else ''
        except (InvalidToken, Exception):
            apt = apt_enc or ''

        conn.execute(text(
            "UPDATE reservas SET nome_solicitante = :nome, contato = :contato, "
            "apartamento = :apt WHERE id = :id"
        ), {"nome": nome, "contato": contato, "apt": apt, "id": row_id})

    # Passo 3: recriar índices originais e remover colunas criptografadas
    bind = op.get_bind()
    insp = sa.inspect(bind)
    idx_names = {i['name'] for i in insp.get_indexes('reservas')}

    with op.batch_alter_table('reservas', schema=None) as batch_op:
        for idx, col in [('ix_reserva_status', 'status'),
                         ('ix_reserva_salao_id', 'salao_id'),
                         ('ix_reserva_data_festa', 'data_festa')]:
            if idx not in idx_names:
                batch_op.create_index(idx, [col], unique=False)
        batch_op.drop_column('_apartamento_enc')
        batch_op.drop_column('_contato_enc')
        batch_op.drop_column('_nome_solicitante_enc')
