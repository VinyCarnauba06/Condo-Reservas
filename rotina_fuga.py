"""
Rotina de Fuga — Plano de Continuidade de Negócio (PCN)
Extrai reservas dos próximos 15 dias e envia por e-mail como CSV de emergência.
"""

import os
import sys
import csv
import io
import logging
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ---------------------------------------------------------------------------
# Carrega o contexto Flask
# ---------------------------------------------------------------------------
from run import app
from app.models import Reserva, Salao, Condominio
from app import db
from sqlalchemy.orm import joinedload


def _exportar_csv(reservas):
    def _san(v):
        s = str(v) if v is not None else ''
        return ("'" + s) if s and s[0] in '=+-@\t' else s

    buf    = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(['Data', 'Condomínio', 'Salão', 'Apartamento', 'Solicitante', 'Contato', 'Status'])
    for r in reservas:
        writer.writerow([
            r.data_festa.strftime('%d/%m/%Y'),
            r.salao.condominio.nome,
            r.salao.nome,
            _san(r.apartamento        or '[Festa do Condomínio]'),
            _san(r.nome_solicitante   or '[Festa do Condomínio]'),
            _san(r.contato            or ''),
            r.status,
        ])
    buf.seek(0)
    return buf.getvalue().encode('utf-8-sig')


def _enviar_email(csv_bytes, data_hoje):
    smtp_server = os.environ['SMTP_SERVER']
    smtp_port   = int(os.environ.get('SMTP_PORT', 587))
    smtp_user   = os.environ['SMTP_USER']
    smtp_pass   = os.environ['SMTP_PASSWORD']
    destino     = os.environ['BACKUP_EMAIL_DESTINO']

    msg = MIMEMultipart()
    msg['From']    = smtp_user
    msg['To']      = destino
    msg['Subject'] = f'🚨 BACKUP DE EMERGÊNCIA - Reservas {data_hoje.strftime("%d/%m/%Y")}'

    corpo = (
        f'Olá,\n\n'
        f'Segue em anexo o CSV de reservas ativas dos próximos 15 dias '
        f'(gerado automaticamente em {data_hoje.strftime("%d/%m/%Y")}).\n\n'
        f'Este e-mail é parte do Plano de Continuidade de Negócio (PCN) da CondoReservas.\n\n'
        f'— Sistema CondoReservas'
    )
    msg.attach(MIMEText(corpo, 'plain', 'utf-8'))

    filename = f'reservas_emergencia_{data_hoje.isoformat()}.csv'
    part = MIMEBase('application', 'octet-stream')
    part.set_payload(csv_bytes)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
    msg.attach(part)

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, destino, msg.as_string())

    logging.info('E-mail enviado para %s', destino)


def main():
    with app.app_context():
        hoje   = date.today()
        limite = hoje + timedelta(days=15)

        reservas = (
            Reserva.query
            .join(Salao)
            .join(Condominio, Salao.condominio_id == Condominio.id)
            .options(joinedload(Reserva.salao).joinedload(Salao.condominio))
            .filter(Reserva.data_festa >= hoje)
            .filter(Reserva.data_festa <= limite)
            .filter(Reserva.status != 'cancelado')
            .order_by(Condominio.nome, Reserva.data_festa)
            .all()
        )

        logging.info('%d reservas encontradas entre %s e %s', len(reservas), hoje, limite)

        csv_bytes = _exportar_csv(reservas)
        _enviar_email(csv_bytes, hoje)
        logging.info('Rotina de fuga concluída com sucesso.')


if __name__ == '__main__':
    missing = [v for v in ('SMTP_SERVER', 'SMTP_USER', 'SMTP_PASSWORD', 'BACKUP_EMAIL_DESTINO')
               if not os.environ.get(v)]
    if missing:
        logging.error('Variáveis de ambiente obrigatórias ausentes: %s', ', '.join(missing))
        sys.exit(1)
    main()
