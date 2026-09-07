import csv
import json
from flask import Blueprint, render_template, request, send_file, redirect, url_for, session, flash, Response
from sqlalchemy.orm import joinedload, selectinload
from app import db, limiter
from app.models import (
    Reserva, Salao, Condominio, Feriado, Bloqueio,
    DocumentoGerado, Credito, Usuario, ItemInventario, RegraRegimento, AuditLog, log_audit,
    sanitize_text, RegraPrecificacao, ReservaAnualUnidade,
    VistoriaTermo, VistoriaFoto, VistoriaItem, ItemProblema,  # [TESTE] vistoria mobile — ver CHANGELOG_VISTORIA_MOBILE.md
)
from app.routes.auth import login_required, admin_required
from app.routes.fiscal_fixo import _garantir_posse_fiscal
from datetime import date, timedelta, datetime, timezone
from xml.sax.saxutils import escape as _xml_escape
import pytz

def get_hoje_br():
    return datetime.now(pytz.timezone('America/Sao_Paulo')).date()
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import CondPageBreak
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, Image, PageBreak, ListFlowable, ListItem
)
import io
import os
import logging

relatorios_bp = Blueprint('relatorios', __name__)

def _excel_safe(valor):
    """Neutraliza Formula/CSV Injection: célula que comece com =, +, -, @
    seria avaliada como fórmula pelo Excel/LibreOffice ao abrir o arquivo."""
    if isinstance(valor, str) and valor[:1] in ('=', '+', '-', '@'):
        return "'" + valor
    return valor

AZUL_ESCURO = colors.HexColor('#1E3A5F')
AZUL_MEDIO  = colors.HexColor('#2E5F8A')
CINZA_PRATA = colors.HexColor('#8E9EAB')
CINZA_LINHA = colors.HexColor('#F2F4F6')
VERMELHO    = colors.HexColor('#FADBD8')
LARANJA     = colors.HexColor('#FDEBD0')
VERDE       = colors.HexColor('#D5F5E3')
ROXO        = colors.HexColor('#E8DAEF')
BRANCO      = colors.white


def ps(size, bold=False, color=colors.black, align=TA_LEFT):
    return ParagraphStyle(
        f's{size}{bold}{color}',
        parent    = getSampleStyleSheet()['Normal'],
        fontSize  = size,
        fontName  = 'Helvetica-Bold' if bold else 'Helvetica',
        textColor = color,
        alignment = align,
        leading   = size + 3
    )


def registrar_documento(tipo, data_inicio, reserva_id=None, condominio_id=None):
    try:
        db.session.add(DocumentoGerado(
            tipo          = tipo,
            reserva_id    = reserva_id,
            condominio_id = condominio_id,
            semana_inicio = data_inicio,
            gerado_em     = datetime.now(timezone.utc)
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logging.warning(f"Falha ao registrar documento [{tipo}] — ignorado.")


def cor_reserva(reserva):
    if reserva.surpresa:              return ROXO
    if reserva.status == 'cancelado': return VERMELHO
    if reserva.status == 'pendente':  return LARANJA
    return BRANCO

def _texto_pagamento(r, credito=None):
    if credito:
        return f'Unidade com crédito\nR$ {credito.valor:.2f}'
    if r.registro_anual:
        regra = r.salao.regra_precificacao
        if r.registro_anual.valor_opcional_usado and regra and regra.valor_opcional:
            return f'ISENTO (taxa)\nR$ {regra.valor_opcional:.2f}'
        return 'ISENTO\nR$ 0,00'
    return f'{r.salao.forma_pagamento}\nR$ {r.salao.valor:.2f}'

def _gep_pdf(r):
    isento_total = r.registro_anual and not r.registro_anual.valor_opcional_usado
    if not r.salao.valor or r.festa_condominio or isento_total:
        return ''
    g = f"G[{'x' if r.boleto_gerado else ' '}]"
    e = f"E[{'x' if r.boleto_enviado else ' '}]"
    p = f"P[{'x' if r.boleto_pago else ' '}]"
    if r.salao.forma_pagamento and 'condomínio' in r.salao.forma_pagamento.lower():
        return f'{g} {e}'
    return f'{g} {e} {p}'

_PALAVRAS_FEMININAS = ('churrasqueira', 'piscina', 'quadra', 'sala', 'brinquedoteca', 'academia')

def _artigo_salao(nome):
    lower = nome.lower()
    for palavra in _PALAVRAS_FEMININAS:
        if palavra in lower:
            return 'A', 'RESERVADA'
    return 'O', 'RESERVADO'

def gerar_pdf_reservas(reservas, titulo, data_inicio, data_fim):
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                leftMargin=1.5*cm, rightMargin=1.5*cm,
                topMargin=1.2*cm, bottomMargin=1.2*cm)
    story  = []

    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')
    logo = Image(logo_path, width=2.5*cm, height=2.5*cm) if os.path.exists(logo_path) \
           else Paragraph('Administradora', ps(14, bold=True, color=AZUL_ESCURO))

    header_table = Table([[
        logo,
        Paragraph('CondoReservas — Sistema de Gestão de Reservas', ps(11, bold=True, color=AZUL_ESCURO, align=TA_CENTER)),
        Paragraph(f'{titulo}<br/>{data_inicio.strftime("%d/%m/%Y")} a {data_fim.strftime("%d/%m/%Y")}',
                  ps(10, bold=True, color=AZUL_ESCURO, align=TA_RIGHT)),
    ]], colWidths=[3*cm, 17*cm, 8.3*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)

    divider = Table([['']], colWidths=[28.3*cm])
    divider.setStyle(TableStyle([
        ('LINEBELOW',    (0, 0), (-1, -1), 2, AZUL_ESCURO),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
    ]))
    story.append(divider)
    story.append(Spacer(1, 0.3*cm))

    # ── Resumo executivo ──────────────────────────────────────────
    total        = len(reservas)
    confirmadas  = sum(1 for r in reservas if r.status == 'confirmado')
    pendentes_n  = sum(1 for r in reservas if r.status == 'pendente')
    canceladas   = sum(1 for r in reservas if r.status == 'cancelado')

    def stat_cell(label, valor, cor):
        inner = Table([
            [Paragraph(str(valor), ParagraphStyle('sv', parent=getSampleStyleSheet()['Normal'],
                fontSize=22, fontName='Helvetica-Bold', textColor=BRANCO, alignment=TA_CENTER, leading=26))],
            [Paragraph(label,      ParagraphStyle('sl', parent=getSampleStyleSheet()['Normal'],
                fontSize=8, fontName='Helvetica', textColor=colors.HexColor('#b0c8e0'), alignment=TA_CENTER, leading=10))],
        ], colWidths=[4.8*cm])
        inner.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), cor),
            ('TOPPADDING',    (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('LEFTPADDING',   (0,0), (-1,-1), 4),
            ('RIGHTPADDING',  (0,0), (-1,-1), 4),
            ('ROUNDEDCORNERS', [6]),
        ]))
        return inner

    AZUL_STAT   = colors.HexColor('#1E3A5F')
    VERDE_STAT  = colors.HexColor('#0F6E56')
    LARANJA_STAT= colors.HexColor('#8A5014')
    VERM_STAT   = colors.HexColor('#7B1E1E')

    stat_row = Table([[
        stat_cell('TOTAL',       total,       AZUL_STAT),
        stat_cell('CONFIRMADAS', confirmadas, VERDE_STAT),
        stat_cell('PENDENTES',   pendentes_n, LARANJA_STAT),
        stat_cell('CANCELADAS',  canceladas,  VERM_STAT),
    ]], colWidths=[6.25*cm]*4)
    stat_row.setStyle(TableStyle([
        ('LEFTPADDING',  (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING',   (0,0), (-1,-1), 0),
        ('BOTTOMPADDING',(0,0), (-1,-1), 0),
    ]))
    story.append(stat_row)
    story.append(Spacer(1, 0.5*cm))
    # ─────────────────────────────────────────────────────────────

    ids_reservas = [r.id for r in reservas]
    creditos_map = {
        c.reserva_id: c
        for c in Credito.query.filter(Credito.reserva_id.in_(ids_reservas)).all()
    } if ids_reservas else {}

    from sqlalchemy import func as _func
    condominio_ids = list({r.salao.condominio_id for r in reservas})
    saloes_por_cond = {
        row[0]: row[1]
        for row in db.session.query(Salao.condominio_id, _func.count(Salao.id))
        .filter(Salao.condominio_id.in_(condominio_ids))
        .group_by(Salao.condominio_id)
        .all()
    } if condominio_ids else {}

    dias_pt = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    h = lambda t: Paragraph(t, ps(8, bold=True, color=BRANCO, align=TA_CENTER))
    rows = [[h('DATA'), h('CONDOMINIO'), h('N APT.'), h('NOME / TELEFONE'),
             h('PAGAMENTO'), h('GEP'), h('OBS.')]]
    row_colors = []

    for i, r in enumerate(reservas):
        rows.append([
            Paragraph(f'{r.data_festa.strftime("%d/%m")}\n{dias_pt[r.data_festa.weekday()]}', ps(7, align=TA_CENTER)),
            Paragraph(r.salao.condominio.nome,             ps(7)),
            Paragraph(_xml_escape(r.apartamento) if r.apartamento else '[Condomínio]',       ps(7, align=TA_CENTER)),
            Paragraph(f'{_xml_escape(r.nome_solicitante) if r.nome_solicitante else "[Festa do Condomínio]"}\n{_xml_escape(r.contato) if r.contato else ""}', ps(7)),
            Paragraph(_texto_pagamento(r, creditos_map.get(r.id)), ps(7)),
            Paragraph(_gep_pdf(r),                         ps(7, align=TA_CENTER)),
            Paragraph(('[CANCELADO] ' if r.status == 'cancelado' else '[PENDENTE] ' if r.status == 'pendente' else '') + (r.observacoes or '—'), ps(7)),
        ])
        row_colors.append((i + 1, cor_reserva(r)))

    table = Table(rows, colWidths=[2.2*cm, 5.0*cm, 1.6*cm, 4.8*cm, 4.5*cm, 3.0*cm, 7.2*cm], repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0),  AZUL_ESCURO),
        ('ALIGN',      (0, 0), (-1, 0),  'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.4, CINZA_PRATA),
        ('LINEBELOW',  (0, 0), (-1, 0),  1.5, AZUL_MEDIO),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
    ]
    for idx in range(1, len(rows)):
        style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), CINZA_LINHA if idx % 2 == 0 else BRANCO))
    for row_idx, cor in row_colors:
        style_cmds.append(('BACKGROUND', (0, row_idx), (-1, row_idx), cor))
    table.setStyle(TableStyle(style_cmds))

    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer

def gerar_pdf_limpezas(reservas, data_inicio, data_fim):
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                leftMargin=1.5*cm, rightMargin=1.5*cm,
                topMargin=1.2*cm, bottomMargin=1.2*cm)
    story  = []

    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')
    logo = Image(logo_path, width=2.5*cm, height=2.5*cm) if os.path.exists(logo_path) \
           else Paragraph('Administradora', ps(14, bold=True, color=AZUL_ESCURO))

    header_table = Table([[
        logo,
        Paragraph('CondoReservas — Sistema de Gestão de Reservas', ps(11, bold=True, color=AZUL_ESCURO, align=TA_CENTER)),
        Paragraph(f'Escala de Limpeza<br/>{data_inicio.strftime("%d/%m/%Y")} a {data_fim.strftime("%d/%m/%Y")}',
                  ps(10, bold=True, color=AZUL_ESCURO, align=TA_RIGHT)),
    ]], colWidths=[3*cm, 17*cm, 8.3*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)

    divider = Table([['']], colWidths=[28.3*cm])
    divider.setStyle(TableStyle([
        ('LINEBELOW',    (0, 0), (-1, -1), 2, AZUL_ESCURO),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
    ]))
    story.append(divider)
    story.append(Spacer(1, 0.3*cm))

    h = lambda t: Paragraph(t, ps(8, bold=True, color=BRANCO, align=TA_CENTER))
    rows = [[h('FESTA'), h('DATA LIMPEZA'), h('CONDOMÍNIO'), h('SALÃO'), h('N APT.'), h('OBS.')]]

    
    dias_pt = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    limpezas = sorted(
        [(r.data_festa + timedelta(days=1), r) for r in reservas if r.status != 'cancelado' and r.zelador],
        key=lambda x: x[0]
    )

    for idx, (data_limpeza, r) in enumerate(limpezas):
        obs_parts = []
        if r.zelador:     obs_parts.append('Zelador')
        if r.observacoes: obs_parts.append(r.observacoes)

        rows.append([
            Paragraph(f'{r.data_festa.strftime("%d/%m")} {dias_pt[r.data_festa.weekday()]}', ps(7, align=TA_CENTER)),
            Paragraph(f'{data_limpeza.strftime("%d/%m")} {dias_pt[data_limpeza.weekday()]}', ps(7, align=TA_CENTER)),
            Paragraph(r.salao.condominio.nome,            ps(7)),
            Paragraph(r.salao.nome,                          ps(7)),
            Paragraph(_xml_escape(r.apartamento) if r.apartamento else '[Condomínio]',       ps(7, align=TA_CENTER)),
            Paragraph(' — '.join(obs_parts),              ps(7)),
        ])

    table = Table(rows, colWidths=[3*cm, 3*cm, 7*cm, 5*cm, 2*cm, 8.3*cm], repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0),  AZUL_ESCURO),
        ('ALIGN',      (0, 0), (-1, 0),  'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.4, CINZA_PRATA),
        ('LINEBELOW',  (0, 0), (-1, 0),  1.5, AZUL_MEDIO),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
    ]
    for idx in range(1, len(rows)):
        style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), CINZA_LINHA if idx % 2 == 0 else BRANCO))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer


# [TESTE] recibo de zelador automatizado — converte valor em reais pra texto
# por extenso (pt-BR), pro corpo do recibo ("R$100,00 (cem reais)"). Cobre
# bem os valores típicos de limpeza (dezenas/centenas); milhares ficam
# simples, sem tratar as exceções de concordância mais raras (não é o caso
# de uso aqui).
def _valor_por_extenso(valor):
    UNI      = ['', 'um', 'dois', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove']
    DEZ19    = ['dez', 'onze', 'doze', 'treze', 'quatorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove']
    DEZENAS  = ['', '', 'vinte', 'trinta', 'quarenta', 'cinquenta', 'sessenta', 'setenta', 'oitenta', 'noventa']
    CENTENAS = ['', 'cento', 'duzentos', 'trezentos', 'quatrocentos', 'quinhentos', 'seiscentos', 'setecentos', 'oitocentos', 'novecentos']

    def ate_999(n):
        if n == 0:
            return ''
        if n == 100:
            return 'cem'
        c, resto = divmod(n, 100)
        partes = []
        if c:
            partes.append(CENTENAS[c])
        if resto:
            if resto < 10:
                partes.append(UNI[resto])
            elif resto < 20:
                partes.append(DEZ19[resto - 10])
            else:
                d, u = divmod(resto, 10)
                partes.append(DEZENAS[d] + (f' e {UNI[u]}' if u else ''))
        return ' e '.join(partes)

    def ate_999999(n):
        if n == 0:
            return 'zero'
        milhar, resto = divmod(n, 1000)
        partes = []
        if milhar:
            partes.append('mil' if milhar == 1 else f'{ate_999(milhar)} mil')
        if resto:
            partes.append(ate_999(resto))
        usa_e = milhar and resto and (resto < 100 or resto % 100 == 0)
        return ' e '.join(partes) if usa_e else ' '.join(partes)

    valor    = round(float(valor), 2)
    reais    = int(valor)
    centavos = round((valor - reais) * 100)
    texto    = f'{ate_999999(reais)} {"real" if reais == 1 else "reais"}'
    if centavos:
        texto += f' e {ate_999(centavos)} {"centavo" if centavos == 1 else "centavos"}'
    return texto


_LOGO_ADMIN_PATH    = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')
_LOGO_SISTEMA_PATH = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoSistema.png')

_RECIBO_TITULO_STYLE = ParagraphStyle('recibo_titulo', parent=getSampleStyleSheet()['Normal'],
    fontSize=17, fontName='Helvetica-BoldOblique', alignment=TA_RIGHT, textColor=colors.black)
_RECIBO_SUBHEADER_STYLE = ParagraphStyle('recibo_subheader', parent=getSampleStyleSheet()['Normal'],
    fontSize=10, fontName='Helvetica-Bold', alignment=TA_CENTER, textColor=AZUL_ESCURO)
_RECIBO_DATA_STYLE = ParagraphStyle('recibo_data', parent=getSampleStyleSheet()['Normal'],
    fontSize=10, fontName='Helvetica-Oblique', alignment=TA_RIGHT, textColor=colors.HexColor('#555555'))
_RECIBO_CORPO_STYLE = ParagraphStyle('recibo_corpo', parent=getSampleStyleSheet()['Normal'],
    fontSize=11.5, fontName='Helvetica', alignment=TA_JUSTIFY, leading=18)
_RECIBO_ASSIN_STYLE = ParagraphStyle('recibo_assin', parent=getSampleStyleSheet()['Normal'],
    fontSize=10, fontName='Helvetica', alignment=TA_CENTER)
_RECIBO_ASSIN_LABEL_STYLE = ParagraphStyle('recibo_assin_label', parent=getSampleStyleSheet()['Normal'],
    fontSize=9.5, fontName='Helvetica-Bold', alignment=TA_CENTER, textColor=AZUL_ESCURO)

def _story_recibo_zelador(r, largura=17*cm):
    # [TESTE] recibo de zelador automatizado. [FIX 2026-07-30] layout
    # simplificado pra bater com a estrutura séria/formal dos outros PDFs do
    # sistema (termos, comunicados, cancelamentos) — Viny não gostou do
    # visual "moderno" (painel colorido, ficha em grade): timbre com as
    # duas logos + "RECIBO", divisor azul (mesmo padrão de
    # gerar_pdf_cancelamentos), parágrafo formal justificado, assinatura.
    # Sem cor de destaque, sem caixas extras — documento financeiro sério.
    cond         = r.salao.condominio
    valor        = round(float(cond.valor_zelador), 2)
    valor_str    = f'{valor:.2f}'.replace('.', ',')
    data_limpeza = r.data_festa + timedelta(days=1)
    hoje_br      = get_hoje_br()
    meses_pt     = ['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
                    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    emissao      = f'{hoje_br.day:02d} de {meses_pt[hoje_br.month]} de {hoje_br.year}'
    apto         = _xml_escape(r.apartamento) if r.apartamento else '[Condomínio]'

    story = []

    # --- Timbre: logos à esquerda, título à direita, divisor azul --------
    logo_admin    = Image(_LOGO_ADMIN_PATH, width=1.8*cm, height=1.8*cm) \
                  if os.path.exists(_LOGO_ADMIN_PATH) else Paragraph('Administradora', _RECIBO_SUBHEADER_STYLE)
    logo_sistema = Image(_LOGO_SISTEMA_PATH, width=2.2*cm, height=1.8*cm) \
                  if os.path.exists(_LOGO_SISTEMA_PATH) else Paragraph('CONDORESERVAS', _RECIBO_SUBHEADER_STYLE)

    logos = Table([[logo_sistema, logo_admin]], colWidths=[2.5*cm, 2.3*cm])
    logos.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))

    header = Table([[
        logos,
        Paragraph('RECIBO', _RECIBO_TITULO_STYLE),
    ]], colWidths=[5.2*cm, largura - 5.2*cm])
    header.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',        (1, 0), (1, 0),   'RIGHT'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
    ]))
    story.append(header)

    divisor = Table([['']], colWidths=[largura])
    divisor.setStyle(TableStyle([
        ('LINEBELOW',    (0, 0), (-1, -1), 2, AZUL_ESCURO),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
    ]))
    story.append(divisor)
    story.append(Spacer(1, 0.8*cm))

    story.append(Paragraph(f'Cidade Exemplo, {emissao}', _RECIBO_DATA_STYLE))
    story.append(Spacer(1, 0.9*cm))

    # --- Corpo formal do recibo -------------------------------------------
    if cond.zelador_pago_direto_fiscal:
        # [TESTE] "morador paga ao fiscal" (hoje: Edifício Bela Vista, HIT
        # Residence) — dinheiro não passa pela Administradora, vai direto pro
        # fiscal em campo. Recibo endereça o condômino pelo nome (não o
        # condomínio) e ganha uma linha extra de assinatura condômino/
        # fiscal, provando o repasse. Estrutura confirmada pelo Viny com
        # o modelo real usado em campo (RECIBO EDIFÍCIO BELA VISTA - EXEMPLO).
        nome_morador = _xml_escape(r.nome_solicitante) if r.nome_solicitante else '[Festa do Condomínio]'
        texto_recibo = (
            f'Recebemos do(a) condômino(a), <b>{nome_morador}</b> do Ed. '
            f'<b>{cond.nome.upper()}</b> do Apto {apto}, valor de R${valor_str} '
            f'({_valor_por_extenso(valor)}), referente a limpeza do salão '
            f'no dia {data_limpeza.strftime("%d/%m/%Y")} da festa realizada '
            f'no dia {r.data_festa.strftime("%d/%m/%Y")}.'
        )
    else:
        texto_recibo = (
            f'Recebemos do condomínio <b>{cond.nome.upper()}</b> o valor de '
            f'R${valor_str} ({_valor_por_extenso(valor)}). Referente à limpeza '
            f'do salão {data_limpeza.strftime("%d/%m/%Y")} da festa realizada '
            f'dia {r.data_festa.strftime("%d/%m/%Y")} do Apto {apto}.'
        )
    story.append(Paragraph(texto_recibo, _RECIBO_CORPO_STYLE))

    # --- Assinaturas --------------------------------------------------------
    story.append(Spacer(1, 1.6*cm))
    story.append(Paragraph('Atenciosamente,', _RECIBO_CORPO_STYLE))
    story.append(Spacer(1, 1.2*cm))

    def _bloco_assinatura(nome, sublabel=None):
        elementos = [
            Paragraph('_____________________________________', _RECIBO_ASSIN_STYLE),
            Paragraph(nome, _RECIBO_ASSIN_LABEL_STYLE),
        ]
        if sublabel:
            elementos.append(Paragraph(sublabel, _RECIBO_ASSIN_STYLE))
        return elementos

    if cond.zelador_pago_direto_fiscal:
        assinaturas = Table([[
            _bloco_assinatura('Administradora', 'CNPJ: 12.345.678/0001-90'),
            _bloco_assinatura('Condômino'),
            _bloco_assinatura('Fiscal'),
        ]], colWidths=[largura/3.0]*3)
        assinaturas.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(assinaturas)
    else:
        for el in _bloco_assinatura('Administradora', 'CNPJ: 12.345.678/0001-90'):
            story.append(el)

    return story


def gerar_pdf_recibos_zelador(reservas, data_inicio, data_fim):
    # [TESTE] recibo de zelador automatizado — um recibo por reserva com
    # zelador=True. CondPageBreak evita quebrar um recibo no meio entre duas
    # páginas. [FIX] reservas de zelador_pago_direto_fiscal (Grand
    # Versailles, Residencial Palmeiras) saíram deste lote — a pedido do Viny, esse
    # tipo de recibo agora só sai anexado ao termo (ver gerar_pdf_termos),
    # porque é entregue em mãos no dia da vistoria/revistoria, não emitido
    # em lote pela administradora.
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=A4,
                leftMargin=2.5*cm, rightMargin=2.5*cm,
                topMargin=1.8*cm, bottomMargin=1.8*cm)
    story  = []

    reservas_validas = [
        r for r in reservas
        if r.status != 'cancelado' and r.zelador and r.salao.condominio.valor_zelador
        and not r.salao.condominio.zelador_pago_direto_fiscal
    ]
    reservas_validas.sort(key=lambda r: r.data_festa)

    for r in reservas_validas:
        story.append(CondPageBreak(7*cm))
        story.extend(_story_recibo_zelador(r))
        story.append(Spacer(1, 1.1*cm))

    if not reservas_validas:
        story.append(Paragraph(
            'Nenhuma reserva com zelador e valor de limpeza configurado neste período.',
            _RECIBO_CORPO_STYLE))

    doc.build(story)
    buffer.seek(0)
    return buffer


def gerar_pdf_comunicados(reservas, data_inicio, data_fim):
    from collections import defaultdict
    import io
    import os
    from datetime import date
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2.5*cm, bottomMargin=2*cm)
    story = []

    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')

    dias_semana_pt = ['Segunda-feira', 'Terça-feira', 'Quarta-feira',
                      'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
    meses_pt       = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                      'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

    # AGRUPAMENTO APENAS POR CONDOMÍNIO
    agrupado = defaultdict(list)
    for r in reservas:
        if r.status != 'cancelado':
            agrupado[r.salao.condominio_id].append(r)

    styles = getSampleStyleSheet()
    style_comunicado = ParagraphStyle('Comunicado', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=28, alignment=TA_CENTER, spaceAfter=15)
    style_data = ParagraphStyle('Data', parent=styles['Normal'], fontName='Helvetica', fontSize=14, alignment=TA_RIGHT)
    style_normal = ParagraphStyle('NormalDoc', parent=styles['Normal'], fontName='Helvetica', fontSize=18, alignment=TA_LEFT, spaceAfter=8, leading=22)
    style_destaque = ParagraphStyle('Destaque', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=22, alignment=TA_CENTER, spaceAfter=10, leading=28)
    style_assinatura = ParagraphStyle('Assinatura', parent=styles['Normal'], fontName='Helvetica', fontSize=16, alignment=TA_LEFT, leading=20)

    first_page = True
    for cond_id, reservas_cond in agrupado.items():
        cond = reservas_cond[0].salao.condominio

        # Respeita flag emite_comunicado
        if not cond.emite_comunicado:
            continue

        # Ordena as reservas do condomínio pela data
        reservas_cond = sorted(reservas_cond, key=lambda x: x.data_festa)

        # Pré-calcula valores que não mudam entre cópias
        saloes_unicos    = list(set(r.salao.nome for r in reservas_cond))
        espaco_flexivel  = max(1.0, 6.0 - (len(reservas_cond) * 1.5))
        copias           = max(1, min(10, cond.qtd_comunicados or 3))

        if len(saloes_unicos) == 1:
            intro = f'Informamos que há uma reserva para utilização de <b>{saloes_unicos[0].upper()}</b> por:'
        else:
            intro = 'Informamos que há reservas para utilização dos seguintes espaços por:'

        for _ in range(copias):
            if not first_page:
                story.append(PageBreak())
            first_page = False

            # 1. CABEÇALHO (Logo e Data) — reconstruído por cópia para evitar reuso de flowables
            data_hoje = get_hoje_br()
            data_str = f'Cidade Exemplo - UF, {data_hoje.day:02d} de {meses_pt[data_hoje.month - 1]} de {data_hoje.year}.'

            if os.path.exists(logo_path):
                img = Image(logo_path, width=2.8*cm, height=2.8*cm)
                img.hAlign = 'LEFT'
                t_header = Table([[img, Paragraph(data_str, style_data)]], colWidths=[4*cm, 13*cm])
                t_header.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('ALIGN', (1,0), (1,0), 'RIGHT'),
                    ('LEFTPADDING', (0,0), (-1,-1), 0),
                ]))
                story.append(t_header)
            else:
                story.append(Paragraph(data_str, style_data))

            story.append(Spacer(1, 1*cm))

            # 2. TÍTULO CENTRALIZADO
            story.append(Paragraph('COMUNICADO', style_comunicado))
            story.append(Spacer(1, 1*cm))

            # 3. DESTINATÁRIO
            story.append(Paragraph('Ao', style_normal))
            story.append(Paragraph(f'Condomínio do Edifício <b>{cond.nome.upper()}</b>', style_normal))
            story.append(Paragraph('Srs. Condôminos', style_normal))
            story.append(Spacer(1, 1*cm))

            # 4. INTRODUÇÃO E LISTAGEM
            story.append(Paragraph(intro, style_normal))
            story.append(Spacer(1, 0.5*cm))

            for r in reservas_cond:
                # [TESTE] comunicado_texto_override — caso raro em que o
                # síndico pede uma descrição customizada da festa (ex: "Arraiá
                # do condomínio"). Se setado, substitui a linha inteira; senão
                # segue o cálculo padrão abaixo.
                if r.comunicado_texto_override:
                    story.append(Paragraph(r.comunicado_texto_override, style_destaque))
                    continue

                dia_sem      = dias_semana_pt[r.data_festa.weekday()]
                nome_display = (r.motivo_festa or '[Festa do Condomínio]') if r.festa_condominio else f'Apto. {_xml_escape(r.apartamento) if r.apartamento else ""}'

                if len(saloes_unicos) == 1:
                    if r.surpresa:
                        texto = f'No dia {r.data_festa.strftime("%d/%m/%Y")}.'
                    else:
                        texto = f'{nome_display} no dia {r.data_festa.strftime("%d/%m/%Y")} - ({dia_sem})'
                else:
                    if r.surpresa:
                        texto = f'No dia {r.data_festa.strftime("%d/%m/%Y")} - <b>{r.salao.nome.upper()}</b>'
                    else:
                        texto = f'{nome_display} no dia {r.data_festa.strftime("%d/%m/%Y")} - ({dia_sem}) - <b>{r.salao.nome.upper()}</b>'

                story.append(Paragraph(texto, style_destaque))

            # 5. ESPAÇADOR + ASSINATURA
            story.append(Spacer(1, espaco_flexivel * cm))
            story.append(Paragraph('Atenciosamente,', style_assinatura))
            story.append(Spacer(1, 1.2*cm))
            story.append(Paragraph('_______________________', style_assinatura))
            story.append(Paragraph('Administradora', style_assinatura))

    doc.build(story)
    buffer.seek(0)
    return buffer

def gerar_pdf_cancelamentos(reservas, data_inicio, data_fim):
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                leftMargin=1.5*cm, rightMargin=1.5*cm,
                topMargin=1.2*cm, bottomMargin=1.2*cm)
    story  = []

    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')
    logo = Image(logo_path, width=2.5*cm, height=2.5*cm) if os.path.exists(logo_path) \
           else Paragraph('Administradora', ps(14, bold=True, color=AZUL_ESCURO))

    header_table = Table([[
        logo,
        Paragraph('CondoReservas — Sistema de Gestão de Reservas', ps(11, bold=True, color=AZUL_ESCURO, align=TA_CENTER)),
        Paragraph(f'Relatório de Cancelamentos<br/>{data_inicio.strftime("%d/%m/%Y")} a {data_fim.strftime("%d/%m/%Y")}',
                  ps(10, bold=True, color=AZUL_ESCURO, align=TA_RIGHT)),
    ]], colWidths=[3*cm, 17*cm, 8.3*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
    ]))
    story.append(header_table)

    divider = Table([['']], colWidths=[28.3*cm])
    divider.setStyle(TableStyle([
        ('LINEBELOW',    (0, 0), (-1, -1), 2, AZUL_ESCURO),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
    ]))
    story.append(divider)
    story.append(Spacer(1, 0.3*cm))

    h = lambda t: Paragraph(t, ps(8, bold=True, color=BRANCO, align=TA_CENTER))
    rows = [[h('DATA FESTA'), h('CONDOMÍNIO'), h('N APT.'), h('NOME / TELEFONE'),
             h('CANCELADO EM'), h('CANCELADO POR'), h('OBS.')]]

    for i, r in enumerate(reservas):
        rows.append([
            Paragraph(r.data_festa.strftime('%d/%m\n%a'),                     ps(7, align=TA_CENTER)),
            Paragraph(r.salao.condominio.nome,                                 ps(7)),
            Paragraph(_xml_escape(r.apartamento) if r.apartamento else '[Condomínio]',                          ps(7, align=TA_CENTER)),
            Paragraph(f'{_xml_escape(r.nome_solicitante) if r.nome_solicitante else "[Festa do Condomínio]"}\n{_xml_escape(r.contato) if r.contato else ""}', ps(7)),
            Paragraph(r.cancelado_em.strftime('%d/%m/%Y %H:%M') if r.cancelado_em else '—', ps(7, align=TA_CENTER)),
            Paragraph(r.cancelado_por or '—',                                  ps(7, align=TA_CENTER)),
            Paragraph(r.observacoes or '—',                                    ps(7)),
        ])

    table = Table(rows, colWidths=[2.2*cm, 5.5*cm, 1.6*cm, 4.8*cm, 3.5*cm, 3.5*cm, 7.2*cm], repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0),  AZUL_ESCURO),
        ('ALIGN',      (0, 0), (-1, 0),  'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.4, CINZA_PRATA),
        ('LINEBELOW',  (0, 0), (-1, 0),  1.5, AZUL_MEDIO),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
    ]
    for idx in range(1, len(rows)):
        style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), VERMELHO if idx % 2 == 0 else colors.HexColor('#FEF2F2')))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer

def gerar_pdf_termos(reservas, data_inicio, data_fim):
    import io as _io
    import base64 as _base64
    from collections import defaultdict
    from pypdf import PdfWriter, PdfReader
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image, PageBreak, KeepTogether
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.pagesizes import A4
    from datetime import timedelta

    W  = 17 * cm
    IW = 16.4 * cm
    C1 = 5.5 * cm
    C2 = 5.5 * cm
    C3 = IW - C1 - C2

    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')

    # [TESTE] Vistoria mobile — embute assinatura digital quando existir
    def _assinatura_img(b64_data_uri, max_width):
        if not b64_data_uri or ',' not in b64_data_uri:
            return None
        try:
            data = _base64.b64decode(b64_data_uri.split(',', 1)[1])
            img  = Image(_io.BytesIO(data))
            aspect = (img.imageHeight / float(img.imageWidth)) if img.imageWidth else 1
            img.drawWidth  = max_width
            img.drawHeight = max_width * aspect
            max_h = 1.6 * cm
            if img.drawHeight > max_h:
                img.drawHeight = max_h
                img.drawWidth  = max_h / aspect if aspect else max_width
            return img
        except Exception:
            logging.exception('Falha ao renderizar assinatura digital no termo')
            return None

    def pj(size, bold=False):
        return ParagraphStyle(f'j{size}{bold}',
            parent=getSampleStyleSheet()['Normal'],
            fontSize=size,
            fontName='Helvetica-Bold' if bold else 'Helvetica',
            alignment=TA_JUSTIFY,
            leading=size + 1)

    def linha(largura):
        t = Table([['']], colWidths=[largura], rowHeights=[0.01*cm])
        t.setStyle(TableStyle([
            ('LINEABOVE',    (0, 0), (-1, -1), 0.8, colors.black),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        return t

    def caixa_vistoria(titulo, data_str, depois=False, termo=None):
        sig_fiscal_img     = _assinatura_img(termo.assinatura_fiscal_b64, C1 - 0.4 * cm) if termo else None
        sig_requerente_img = _assinatura_img(termo.assinatura_requerente_b64, C2 - 0.4 * cm) if termo else None

        # [FEATURE] horário de abertura do termo — capturado automaticamente
        # pelo sistema (não mais o timestamp de preenchido_em, que podia
        # ficar bem depois do horário real numa vistoria offline que só
        # sincroniza mais tarde). Só uma linha, sem distinguir "marcado" de
        # "realizado" no papel — a pedido do Viny, fica mais simples assim.
        rotulo_hora = 'Hora da revistoria' if depois else 'Hora da vistoria'
        hora_realizada_txt = (
            f'{rotulo_hora}: {termo.hora_realizada}<br/>' if (termo and termo.hora_realizada)
            else f'{rotulo_hora}: _____<br/>'
        )

        sign_row = [
            sig_fiscal_img if sig_fiscal_img else linha(C1),
            sig_requerente_img if sig_requerente_img else linha(C2),
            Paragraph(
                f'{hora_realizada_txt}Data: {data_str}',
                ps(11)),
        ]
        nome_req = (termo.nome_assinante_requerente if termo and termo.nome_assinante_requerente else 'Requerente')
        sign_labels = [
            Paragraph(
                '<b>Funcionário ou Síndico</b>' + (' <font size=8>(assinado digitalmente)</font>' if termo else ''),
                ps(11)),
            Paragraph(
                f'<b>{nome_req}</b>' + (' <font size=8>(assinado digitalmente)</font>' if termo else ''),
                ps(11)),
            '',
        ]
        inner_data = [sign_row, sign_labels]
        span_start = 2
        if not depois:
            obs_txt = termo.observacoes if (termo and termo.observacoes) else None
            inner_data += [
                [Paragraph('<b>Observações:</b>', ps(11)), '', ''],
                [Paragraph(obs_txt, ps(10)), '', ''] if obs_txt else [linha(IW), '', ''],
                [linha(IW), '', ''] if not obs_txt else ['', '', ''],
            ]
            span_rows = 3
        else:
            inner_data += [
                [linha(IW), '', ''],
                [linha(IW), '', ''],
            ]
            span_rows = 2
        style_cmds = [
            ('TOPPADDING',   (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]
        for i in range(span_start, span_start + span_rows):
            style_cmds.append(('SPAN', (0, i), (2, i)))
        inner = Table(inner_data, colWidths=[C1, C2, C3])
        inner.setStyle(TableStyle(style_cmds))
        box = Table([
            [Paragraph(f'<b>{titulo}</b>', ps(9))],
            [inner],
        ], colWidths=[W])
        box.setStyle(TableStyle([
            ('BOX',          (0, 0), (-1, -1), 1, colors.black),
            ('TOPPADDING',   (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
            ('LEFTPADDING',  (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        return box

    # [TESTE] fotos de problema no termo impresso — antes ficavam só
    # acessíveis pela galeria (relatorios.ver_fotos_vistoria), que a conta
    # fiscal nem consegue abrir. Anexa como página(s) extra no PDF, depois
    # das caixas de vistoria/revistoria.
    def _pagina_fotos(titulo, fotos):
        elementos = [
            Spacer(1, 0.2*cm),
            Paragraph(f'<b>{titulo}</b>',
                ParagraphStyle('ft', parent=getSampleStyleSheet()['Normal'],
                    fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER,
                    spaceBefore=0, spaceAfter=6)),
        ]
        imgs = []
        for foto in fotos:
            try:
                img = Image(_io.BytesIO(foto.foto_data))
            except Exception:
                logging.exception('Falha ao renderizar foto de vistoria (id=%s) no termo', foto.id)
                continue
            aspect = (img.imageHeight / float(img.imageWidth)) if img.imageWidth else 1
            img.drawWidth  = 7.8 * cm
            img.drawHeight = 7.8 * cm * aspect
            max_h = 9 * cm
            if img.drawHeight > max_h:
                img.drawHeight = max_h
                img.drawWidth  = max_h / aspect if aspect else 7.8 * cm
            imgs.append(img)
        if not imgs:
            return []
        linhas = [imgs[i:i + 2] for i in range(0, len(imgs), 2)]
        for linha in linhas:
            while len(linha) < 2:
                linha.append('')
            t = Table([linha], colWidths=[8.2 * cm, 8.2 * cm])
            t.setStyle(TableStyle([
                ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING',   (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
            ]))
            elementos.append(t)
        return elementos

    def story_solicitacao_apoio(r):
        # [TESTE] Condominio.termo_na_portaria — Residencial Costa Azul e Edifício Panorama já têm o
        # termo de requisição físico na portaria (fiscal próprio seg-sáb,
        # Administradora só dá apoio pontual no domingo). Pra esses, o documento não é
        # o termo legal completo (regras/inventário/assinaturas) — é só um
        # aviso pro fiscal de plantão saber que precisa ir lá fazer a
        # vistoria e/ou revistoria naquele domingo.
        cond  = r.salao.condominio
        story = []
        logo = Image(logo_path, width=2.2*cm, height=2.2*cm) if os.path.exists(logo_path) \
               else Paragraph('Administradora', ps(12, bold=True))
        story.append(logo)
        story.append(Spacer(1, 1.2*cm))
        story.append(Paragraph('SOLICITAÇÃO DE APOIO FISCAL',
            ParagraphStyle('sol_titulo', parent=getSampleStyleSheet()['Normal'],
                fontSize=18, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=6)))
        story.append(Paragraph('(domingo — fiscal próprio cobre o restante da semana)',
            ps(10, color=colors.grey, align=TA_CENTER)))
        story.append(Spacer(1, 1*cm))

        acoes = []
        if cond.precisa_vistoria_em(r.data_vistoria):
            data_acao = r.data_vistoria if r.vistoria and r.data_vistoria else r.data_festa
            acoes.append(('VISTORIA', data_acao))
        if cond.precisa_vistoria_em(r.dia_revistoria):
            acoes.append(('REVISTORIA', r.dia_revistoria))

        for acao, data_acao in acoes:
            texto = (
                f'Fazer <b>{acao}</b> no <b>{r.salao.nome}</b> do Edifício <b>{cond.nome.upper()}</b> '
                f'no domingo, dia <b>{data_acao.strftime("%d/%m/%Y") if data_acao else "—"}</b>.'
            )
            story.append(Paragraph(texto, ps(13, align=TA_LEFT)))
            story.append(Spacer(1, 0.5*cm))

        story.append(Spacer(1, 0.6*cm))
        story.append(Paragraph(
            '<i>O termo de requisição e responsabilidade já está na portaria do condomínio — '
            'não é necessário levar nem preencher termo aqui.</i>',
            ps(10, color=colors.grey)))
        return story

    def story_reserva(r):
        story = []
        if r.salao.combo and r.salao.grupo:
            saloes_individuais = Salao.query.filter_by(
                condominio_id=r.salao.condominio_id,
                grupo=r.salao.grupo,
                combo=False
            ).all()
            alvos = saloes_individuais if saloes_individuais else [r.salao]
        else:
            alvos = [r.salao]

        # [TESTE] problema persistente por item — uma query por reserva (não
        # por item, pra não reintroduzir o N+1 que já corrigi aqui mesmo).
        _salao_ids_reserva = [s.id for s in alvos]
        problemas_reserva = {
            (p.salao_id, p.descricao_item): p
            for p in ItemProblema.query.filter(ItemProblema.salao_id.in_(_salao_ids_reserva)).all()
        } if _salao_ids_reserva else {}

        first_salao = True
        for salao in alvos:
            if not first_salao:
                story.append(PageBreak())
            first_salao = False

            cond            = r.salao.condominio
            data_vistoria   = r.data_vistoria if r.vistoria and r.data_vistoria else r.data_festa
            # [TESTE] usa a property (respeita data_revistoria_override) em vez
            # de recalcular +1 dia aqui — fonte única em models.Reserva.dia_revistoria.
            data_revistoria = r.dia_revistoria

            # [FEATURE] festa em dias seguidos, mesma unidade/salão — o 2º dia
            # não gera termo próprio (vistoria_pareada_com_id aponta pra cá),
            # então o termo da 1ª reserva precisa citar as duas datas de uso.
            filho_pareado = Reserva.query.filter_by(
                vistoria_pareada_com_id=r.id
            ).filter(Reserva.status != 'cancelado').first()
            if filho_pareado:
                utilizacao_texto = f'{r.data_festa.strftime("%d/%m/%Y")} e {filho_pareado.data_festa.strftime("%d/%m/%Y")}'
                dias_uso_texto   = f'<b>{r.data_festa.strftime("%d/%m/%Y")}</b> e <b>{filho_pareado.data_festa.strftime("%d/%m/%Y")}</b>'
            else:
                utilizacao_texto = r.data_festa.strftime("%d/%m/%Y")
                dias_uso_texto   = f'<b>{r.data_festa.strftime("%d/%m/%Y")}</b>'
            # [TESTE] fiscal próprio (seg-sáb) — mesma regra do filtro que
            # decide se a reserva entra no lote (acima, antes de chamar
            # story_reserva). Aqui decide qual das duas caixas (antes/depois
            # da festa) efetivamente imprime. [FIX] usa precisa_vistoria_em()
            # em vez de só "is not None" — reserva criada antes do condomínio
            # marcar fiscal_proprio_seg_sab pode ter data_vistoria congelado
            # num dia de semana; reconsulta a regra atual.
            precisa_vistoria_age   = cond.precisa_vistoria_em(r.data_vistoria)
            precisa_revistoria_age = cond.precisa_vistoria_em(r.dia_revistoria)

            # [TESTE] Vistoria mobile — termo digital preenchido, se existir (pré-carregado em _termos_map)
            termo_vistoria   = _termos_map.get((r.id, 'vistoria'))
            termo_revistoria = _termos_map.get((r.id, 'revistoria'))
            itens_status = {vi.item_inventario_id: vi for vi in termo_vistoria.itens} if termo_vistoria else {}

            logo = Image(logo_path, width=1.8*cm, height=1.8*cm) if os.path.exists(logo_path) \
                   else Paragraph('Administradora', ps(9, bold=True, color=AZUL_ESCURO))

            _art, _ = _artigo_salao(salao.nome)
            _prep   = 'da' if _art == 'A' else 'do'
            titulo_termo = f'Termo de Requisição e Responsabilidade para uso {_prep} {salao.nome}'

            header_content = Table([
                [Paragraph(f'<b><u>{cond.nome.upper()}</u></b>',
                    ParagraphStyle('ch', parent=getSampleStyleSheet()['Normal'],
                        fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER))],
                [Paragraph(f'<b>{titulo_termo}</b>',
                    ParagraphStyle('ct', parent=getSampleStyleSheet()['Normal'],
                        fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=2))],
                [Paragraph(
                    f'(Vistoria: {data_vistoria.strftime("%d/%m/%Y")} &nbsp;|&nbsp; '
                    f'Horário: {r.hora_vistoria if r.hora_vistoria else "_________"} &nbsp;|&nbsp; '
                    f'Utilização: {utilizacao_texto})'
                    + ('<b> SOLICITAR ZELADOR</b>' if r.zelador else ''),
                    ParagraphStyle('cs', parent=getSampleStyleSheet()['Normal'],
                        fontSize=10, alignment=TA_CENTER))],
            ], colWidths=[14.8*cm])
            header_content.setStyle(TableStyle([
                ('TOPPADDING',   (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
                ('LEFTPADDING',  (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ]))

            header = Table([[logo, header_content]], colWidths=[2.2*cm, 14.8*cm])
            header.setStyle(TableStyle([
                ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING',  (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ]))
            story.append(header)

            divisor = Table([['']], colWidths=[W])
            divisor.setStyle(TableStyle([
                ('LINEBELOW',    (0, 0), (-1, -1), 1, AZUL_ESCURO),
                ('TOPPADDING',   (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
            ]))
            story.append(divisor)
            story.append(Spacer(1, 0.15*cm))

            _nome    = _xml_escape((r.nome_solicitante or "").upper())
            _contato = _xml_escape(r.contato or "")
            _apto    = _xml_escape(r.apartamento or "")
            intro = (
                f'Eu, <b>{_nome} – {_contato}</b> morador(a) do '
                f'apartamento <b>{_apto}</b>, requisito para meu uso privativo, o '
                f'<b>{salao.nome}</b> no <b>{cond.nome}</b> no dia '
                f'{dias_uso_texto}. Face de tal utilização, pelo presente '
                f'e na melhor forma de direito, comprometo-me e responsabilizo-me pelo a seguir discriminado:'
            )
            story.append(Paragraph(intro, pj(10)))
            story.append(Spacer(1, 0.15*cm))

            regras_default = [
                'O Salão de Festas deve ser utilizado de tal forma a não perturbar a boa ordem do Edifício e <b><u>sossego dos demais condôminos (USO DE SOM ALTO)</u></b>, com observância de todas as normas da Convenção do Condomínio e do seu Regimento Interno.',
                'O Salão de Festas será utilizado por mim, minha família e convidados, de tal forma a não perturbar a boa ordem do Edifício e sossego dos demais condôminos, com observância de todas as normas da Convenção do Condomínio e do seu Regimento Interno.',
                'No prazo de 04 (quatro) horas após a sua utilização, o Salão de Festas será recolocado em suas condições originais de recebimento e perfeitamente limpo.',
                'Corre por minha integral responsabilidade todo e qualquer dano sofrido pelo revestimento (pintura, vidros e balcão), mesas, cadeiras e acessórios dos banheiros que guarnecem o referido Salão, durante o período de sua utilização.',
                f'Autorizo a administração do Condomínio do <u>{cond.nome}</u> a reparar todo e qualquer dano ocasionado durante a utilização do Salão de Festas, bem como, acrescentar no boleto de pagamento da taxa condominial, o valor correspondente a recomposição dos bens.',
                'Relação de pertences do salão de festas:',
            ]
            regras_obj = cond.regras[0] if cond.regras else None
            regras_extras = [l.strip() for l in regras_obj.texto.strip().split('\n') if l.strip()] if regras_obj else []
            todas_regras = regras_default + regras_extras
            items = [[Paragraph(f'{chr(8560+i)}  {t}', pj(10))] for i, t in enumerate(todas_regras)]
            regras_data = items
            regras_table = Table(regras_data, colWidths=[W])
            regras_table.setStyle(TableStyle([
                ('TOPPADDING',    (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('LEFTPADDING',   (0, 0), (-1, -1), 12),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
            ]))
            story.append(regras_table)
            story.append(Spacer(1, 0.15*cm))

            itens = [i for i in salao.inventario if i.ativo]
            if itens:
                import math
                itens_sorted = sorted(itens, key=lambda x: x.ordem)
                n            = len(itens_sorted)
                chunk        = math.ceil(n / 3)
                cols         = [itens_sorted[i:i + chunk] for i in range(0, n, chunk)]
                while len(cols) < 3:
                    cols.append([])
                col1, col2, col3 = cols[0], cols[1], cols[2]
                max_rows = max(len(col1), len(col2), len(col3))

                # [TESTE] fonte adaptativa do inventário — inventário grande
                # (muitos itens) estourava a página ou empurrava a caixa de
                # vistoria pra segunda página. Reduz o tamanho da fonte
                # conforme o número de linhas por coluna cresce, pra caber
                # numa página só na maioria dos casos. Escalona por
                # max_rows (linhas por coluna), que é o que de fato
                # determina a altura ocupada — não pelo total de itens.
                if max_rows <= 10:
                    fs_inv, ld_inv = 9.5, 11
                elif max_rows <= 16:
                    fs_inv, ld_inv = 9, 10
                elif max_rows <= 22:
                    fs_inv, ld_inv = 8, 9
                elif max_rows <= 30:
                    fs_inv, ld_inv = 7, 8
                else:
                    fs_inv, ld_inv = 6, 7

                inv_style = ParagraphStyle('inv',
                    parent=getSampleStyleSheet()['Normal'],
                    fontSize=fs_inv, fontName='Helvetica', leading=ld_inv, leftIndent=4)
                empty = Paragraph('', inv_style)

                def _item_txt(it):
                    vi = itens_status.get(it.id)
                    if vi is None:
                        return f'( )  {it.descricao}'
                    marca = 'X' if vi.presente else ' '
                    txt   = f'({marca})  {it.descricao}'
                    # [TESTE] se o fiscal não retypou a obs nesta vistoria mas
                    # existe um problema persistente pra esse item, mantém
                    # aparecendo no termo até alguém marcar como resolvido.
                    obs = vi.observacao or (
                        problemas_reserva.get((it.salao_id, it.descricao)).observacao
                        if problemas_reserva.get((it.salao_id, it.descricao)) else None
                    )
                    if obs:
                        txt += f' <i>— {obs}</i>'
                    return txt

                inv_data = []
                for i in range(max_rows):
                    c1 = Paragraph(_item_txt(col1[i]), inv_style) if i < len(col1) else empty
                    c2 = Paragraph(_item_txt(col2[i]), inv_style) if i < len(col2) else empty
                    c3 = Paragraph(_item_txt(col3[i]), inv_style) if i < len(col3) else empty
                    inv_data.append([c1, c2, c3])
                cw = W / 3
                inv_table = Table(inv_data, colWidths=[cw, cw, cw], splitByRow=1)
                inv_table.setStyle(TableStyle([
                    ('TOPPADDING',   (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
                    ('LEFTPADDING',  (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ]))
                story.append(inv_table)
            else:
                story.append(Paragraph('<i>(Inventário não cadastrado para este salão)</i>', ps(9)))

            story.append(Spacer(1, 0.15*cm))
            story.append(Paragraph(
                '<b><i><u>OBS: REVISTORIA ATÉ AS 18:00h. CASO O CONDÔMINO NÃO CUMPRA, SOFRERÁ AS '
                'PENALIDADES INSCRITAS NO REGIMENTO INTERNO.</u></i></b>',
                ParagraphStyle('obs_v', parent=getSampleStyleSheet()['Normal'],
                    fontSize=10, fontName='Helvetica-BoldOblique', leading=12,
                    spaceBefore=0, spaceAfter=0, alignment=TA_JUSTIFY)))
            if precisa_vistoria_age or precisa_revistoria_age:
                story.append(Spacer(1, 0.15*cm))
                titulo_vistoria = Paragraph('<b>VISTORIA</b>',
                    ParagraphStyle('vt', parent=getSampleStyleSheet()['Normal'],
                        fontSize=11, fontName='Helvetica-Bold', alignment=TA_CENTER,
                        spaceBefore=0, spaceAfter=2))
                # [FIX 2026-07-30] KeepTogether — a caixa (Table com borda)
                # podia quebrar no meio entre duas páginas quando sobrava
                # pouco espaço (ex: inventário grande empurrando pro fim da
                # página): título "Antes da festa" ficava numa página e as
                # linhas de assinatura na seguinte. Agora pula inteira. O
                # título "VISTORIA" vai junto com a primeira caixa, pra não
                # ficar órfão sozinho no fim da página anterior.
                if precisa_vistoria_age:
                    story.append(KeepTogether([
                        titulo_vistoria,
                        caixa_vistoria('Antes da festa', data_vistoria.strftime('%d/%m/%Y'), termo=termo_vistoria),
                    ]))
                    story.append(Spacer(1, 0.1*cm))
                else:
                    story.append(titulo_vistoria)
                if precisa_revistoria_age:
                    story.append(KeepTogether(
                        caixa_vistoria('Depois da festa', data_revistoria.strftime('%d/%m/%Y'), depois=True, termo=termo_revistoria)))

            # [TESTE] fotos de problema — página extra só se tiver algo pra
            # mostrar; cada termo (vistoria/revistoria) leva sua própria seção.
            fotos_vistoria   = termo_vistoria.fotos if (precisa_vistoria_age and termo_vistoria) else []
            fotos_revistoria = termo_revistoria.fotos if (precisa_revistoria_age and termo_revistoria) else []
            if fotos_vistoria or fotos_revistoria:
                story.append(PageBreak())
                story.append(Paragraph(f'<b>Fotos de Problemas — {salao.nome}</b>',
                    ParagraphStyle('fp', parent=getSampleStyleSheet()['Normal'],
                        fontSize=12, fontName='Helvetica-Bold', alignment=TA_CENTER,
                        spaceBefore=0, spaceAfter=4)))
                if fotos_vistoria:
                    story.extend(_pagina_fotos('Vistoria (antes da festa)', fotos_vistoria))
                if fotos_revistoria:
                    story.extend(_pagina_fotos('Revistoria (depois da festa)', fotos_revistoria))

            # [TESTE] recibo de zelador "morador paga ao fiscal" (Grand
            # Versailles, Residencial Palmeiras) — a pedido do Viny, sai junto do
            # termo (entregue em mãos no dia), não no lote avulso de
            # recibos-zelador. Só entra se a reserva pediu zelador e o
            # condomínio tem valor_zelador configurado.
            if r.zelador and cond.zelador_pago_direto_fiscal and cond.valor_zelador:
                story.append(PageBreak())
                story.extend(_story_recibo_zelador(r))

        return story

    # Agrupa reservas por condomínio mantendo ordem
    writer      = PdfWriter()
    conds_vistos = []
    reservas_por_cond = defaultdict(list)
    for r in reservas:
        if r.status == 'cancelado':
            continue
        cid = r.salao.condominio_id
        if cid not in conds_vistos:
            conds_vistos.append(cid)
        reservas_por_cond[cid].append(r)

    regimentos_anexados_termos = set()
    reservas_validas = [r for r in reservas if r.status != 'cancelado']

    # [FIX] N+1: story_reserva fazia 2 queries de VistoriaTermo por reserva
    # (x2 se o salão fosse combo, uma por salão individual) — num lote de
    # termos-comunicados sem filtro de condomínio isso virava centenas de
    # round-trips síncronos ao Postgres e estourava o WORKER TIMEOUT do
    # Gunicorn (30s). Carrega tudo de uma vez e resolve por dict em memória.
    _reserva_ids = [r.id for r in reservas_validas]
    _termos_map  = {}
    if _reserva_ids:
        for _t in (VistoriaTermo.query
                   .filter(VistoriaTermo.reserva_id.in_(_reserva_ids))
                   .options(joinedload(VistoriaTermo.itens))
                   .all()):
            _termos_map[(_t.reserva_id, _t.momento)] = _t

    for r in reservas_validas:
        cond = r.salao.condominio
        # Não emite termo se condomínio desativou ou se é festa do próprio condomínio
        if not cond.emite_termo or r.festa_condominio:
            continue

        # [FEATURE] 2º dia de vistoria combinada não tem termo próprio — o
        # termo sai só na reserva "dona" (1º dia), com as duas datas juntas.
        if r.vistoria_pareada_com_id:
            continue

        # [TESTE] fiscal próprio (seg-sáb) — se nem a vistoria nem a
        # revistoria dessa reserva são da administradora (fiscal próprio
        # cobre os dois, dia normal de semana), não imprime página nenhuma
        # pra ela. Ver Condominio.precisa_vistoria_em() em app/models.py.
        # [FIX] usa precisa_vistoria_em() pra vistoria também (não só "is
        # None") — reserva antiga pode ter data_vistoria congelado de antes
        # do condomínio marcar fiscal_proprio_seg_sab.
        if not cond.precisa_vistoria_em(r.data_vistoria) and not cond.precisa_vistoria_em(r.dia_revistoria):
            continue

        cid      = r.salao.condominio_id
        buf_r    = _io.BytesIO()
        doc_r    = SimpleDocTemplate(buf_r, pagesize=A4,
                       leftMargin=2*cm, rightMargin=2*cm,
                       topMargin=1*cm, bottomMargin=1*cm)
        # [TESTE] termo_na_portaria — troca o termo legal completo por um
        # aviso simples de apoio fiscal (ver story_solicitacao_apoio acima).
        doc_r.build(story_solicitacao_apoio(r) if cond.termo_na_portaria else story_reserva(r))
        buf_r.seek(0)

        for page in PdfReader(buf_r).pages:
            writer.add_page(page)

        cond = r.salao.condominio
        if cond and cond.regimento_pdf_data and cid not in regimentos_anexados_termos:
            try:
                for page in PdfReader(_io.BytesIO(cond.regimento_pdf_data)).pages:
                    writer.add_page(page)
                regimentos_anexados_termos.add(cid)
            except Exception as e:
                logging.error(f"Erro ao anexar regimento cond {cid}: {e}")

    final_buffer = _io.BytesIO()
    writer.write(final_buffer)
    final_buffer.seek(0)
    return final_buffer

def gerar_excel_reservas(reservas, titulo, data_inicio, data_fim):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Reservas"

    AZUL_HEADER = "1E3A5F"
    AZUL_ALT    = "EBF2FA"

    header_font  = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill  = PatternFill("solid", fgColor=AZUL_HEADER)
    alt_fill     = PatternFill("solid", fgColor=AZUL_ALT)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align   = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    thin_side    = Side(style="thin", color="CCCCCC")
    thin_border  = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    # Título
    ws.merge_cells("A1:H1")
    ws["A1"] = f"CondoReservas — {titulo}"
    ws["A1"].font  = Font(name="Calibri", bold=True, size=14, color=AZUL_HEADER)
    ws["A1"].alignment = center_align
    ws.row_dimensions[1].height = 24

    ws.merge_cells("A2:H2")
    ws["A2"] = f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    ws["A2"].font      = Font(name="Calibri", size=10, color="555555")
    ws["A2"].alignment = center_align
    ws.row_dimensions[2].height = 18

    ws.append([])

    headers = ["DATA", "CONDOMÍNIO", "SALÃO", "APTO.", "SOLICITANTE", "HORÁRIO", "VISTORIA", "STATUS"]
    ws.append(headers)
    header_row = ws.max_row
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center_align
        cell.border    = thin_border
    ws.row_dimensions[header_row].height = 22
    ws.freeze_panes = f"A{header_row + 1}"

    STATUS_FILLS = {
        "confirmado": PatternFill("solid", fgColor="D5F5E3"),
        "pendente":   PatternFill("solid", fgColor="FDEBD0"),
        "cancelado":  PatternFill("solid", fgColor="FADBD8"),
    }

    for i, r in enumerate(reservas):
        vistoria_str = r.data_vistoria.strftime("%d/%m/%Y") if r.vistoria and r.data_vistoria else "Mesmo dia"
        row_data = [
            r.data_festa.strftime("%d/%m/%Y"),
            r.salao.condominio.nome,
            r.salao.nome,
            _excel_safe(r.apartamento) or "[Condomínio]",
            _excel_safe(r.nome_solicitante) or "[Festa do Condomínio]",
            r.horario or "—",
            vistoria_str,
            r.status.capitalize(),
        ]
        ws.append(row_data)
        data_row = ws.max_row
        fill = STATUS_FILLS.get(r.status, PatternFill("solid", fgColor=("FFFFFF" if i % 2 == 0 else AZUL_ALT)))
        for col_idx in range(1, 9):
            cell = ws.cell(row=data_row, column=col_idx)
            if col_idx in (1, 4, 6, 7, 8):
                cell.alignment = center_align
            else:
                cell.alignment = left_align
            cell.border = thin_border
            if col_idx == 8:
                cell.fill = fill
            elif i % 2 == 1:
                cell.fill = PatternFill("solid", fgColor=AZUL_ALT)
        ws.row_dimensions[data_row].height = 18

    col_widths = [13, 28, 22, 8, 26, 12, 13, 14]
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def gerar_excel_fds(reservas, data_inicio, data_fim):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Fim de Semana"

    AMARELO   = "FFFF00"
    CINZA     = "D9D9D9"
    BRANCO    = "FFFFFF"
    PRETO     = "000000"

    thin_side = Side(style="thin", color="000000")
    border    = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    center    = Alignment(horizontal="center", vertical="center")
    left      = Alignment(horizontal="left",   vertical="center")

    DIAS_PT = {0:"SEGUNDA",1:"TERÇA",2:"QUARTA",3:"QUINTA",4:"SEXTA",5:"SÁBADO",6:"DOMINGO"}

    from collections import OrderedDict
    grupos = OrderedDict()
    for r in reservas:
        grupos.setdefault(r.data_festa, []).append(r)

    current_row = 1
    for data, lista in grupos.items():
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=7)
        cell           = ws.cell(row=current_row, column=1)
        cell.value     = f"RESERVA SALÃO DE FESTA {data.strftime('%d/%m/%Y')}"
        cell.font      = Font(name="Calibri", bold=True, size=12, color=PRETO)
        cell.fill      = PatternFill("solid", fgColor=AMARELO)
        cell.alignment = center
        cell.border    = border
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=7)
        cell           = ws.cell(row=current_row, column=1)
        cell.value     = DIAS_PT[data.weekday()]
        cell.font      = Font(name="Calibri", bold=True, size=11, color=PRETO)
        cell.fill      = PatternFill("solid", fgColor=CINZA)
        cell.alignment = center
        cell.border    = border
        ws.row_dimensions[current_row].height = 18
        current_row += 1

        headers = ["", "CONDOMÍNIO", "APT", "FESTA", "VISTORIA", "HORA", "OBSERVAÇÕES"]
        for col_idx, h in enumerate(headers, 1):
            cell           = ws.cell(row=current_row, column=col_idx)
            cell.value     = h
            cell.font      = Font(name="Calibri", bold=True, size=10, color=PRETO)
            cell.fill      = PatternFill("solid", fgColor=CINZA)
            cell.alignment = center
            cell.border    = border
        ws.row_dimensions[current_row].height = 18
        current_row += 1

        for r in lista:
            if getattr(r, 'tipo_fds', None) == 'revistoria':
                vistoria_str = f"{r.dia_fds_efetivo.strftime('%d/%m')} (revist.)"
            else:
                vistoria_str = r.data_vistoria.strftime("%d/%m") if r.data_vistoria else ""
            hora_str     = r.hora_vistoria if r.hora_vistoria else ""
            row_data = [
                "TERMO",
                r.salao.condominio.nome,
                _excel_safe(r.apartamento) or "",
                r.data_festa.strftime("%d/%m"),
                vistoria_str,
                hora_str,
                "",
            ]
            for col_idx, val in enumerate(row_data, 1):
                cell           = ws.cell(row=current_row, column=col_idx)
                cell.value     = val
                cell.font      = Font(name="Calibri", size=10)
                cell.fill      = PatternFill("solid", fgColor=BRANCO)
                cell.alignment = center if col_idx != 2 else left
                cell.border    = border
            ws.row_dimensions[current_row].height = 16
            current_row += 1

        current_row += 1

    col_widths = [8, 28, 8, 10, 10, 8, 24]
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def gerar_word_reservas(reservas, titulo, data_inicio, data_fim):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ALIGN_VERTICAL
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    import struct

    doc = Document()

    # Margens
    for section in doc.sections:
        section.top_margin    = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    def set_cell_bg(cell, hex_color):
        tc   = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd  = OxmlElement("w:shd")
        shd.set(qn("w:val"),   "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"),  hex_color)
        tcPr.append(shd)

    def set_cell_border(cell):
        tc   = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcBorders = OxmlElement("w:tcBorders")
        for side in ("top", "left", "bottom", "right"):
            border = OxmlElement(f"w:{side}")
            border.set(qn("w:val"),  "single")
            border.set(qn("w:sz"),   "4")
            border.set(qn("w:color"), "CCCCCC")
            tcBorders.append(border)
        tcPr.append(tcBorders)

    # Header
    logo_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'LogoAdministradora.png')

    header_tbl = doc.add_table(rows=1, cols=2)
    header_tbl.style = "Table Grid"
    header_tbl.rows[0].cells[0].merge(header_tbl.rows[0].cells[0])

    if os.path.exists(logo_path):
        run = header_tbl.rows[0].cells[0].paragraphs[0].add_run()
        run.add_picture(logo_path, width=Cm(2))
    else:
        header_tbl.rows[0].cells[0].paragraphs[0].add_run("Administradora").bold = True

    p_title = header_tbl.rows[0].cells[1].paragraphs[0]
    p_title.text = f"CondoReservas — {titulo}"
    p_title.runs[0].bold = True
    p_title.runs[0].font.size = Pt(13)
    p_title.runs[0].font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)

    p_range = header_tbl.rows[0].cells[1].add_paragraph(
        f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")
    p_range.runs[0].font.size = Pt(10)
    p_range.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    for cell in header_tbl.rows[0].cells:
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    doc.add_paragraph()

    # Resumo executivo
    stats_tbl = doc.add_table(rows=2, cols=4)
    labels = ["Total de Reservas", "Confirmadas", "Pendentes", "Canceladas"]
    counts = [
        len(reservas),
        sum(1 for r in reservas if r.status == "confirmado"),
        sum(1 for r in reservas if r.status == "pendente"),
        sum(1 for r in reservas if r.status == "cancelado"),
    ]
    STAT_COLORS = ["1E3A5F", "1E5F3A", "5F4A1E", "5F1E1E"]
    for col_idx, (label, count, color) in enumerate(zip(labels, counts, STAT_COLORS)):
        lbl_cell = stats_tbl.rows[0].cells[col_idx]
        cnt_cell = stats_tbl.rows[1].cells[col_idx]
        lbl_cell.paragraphs[0].text = label
        lbl_cell.paragraphs[0].runs[0].font.size = Pt(9)
        lbl_cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        lbl_cell.paragraphs[0].runs[0].bold = True
        lbl_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_cell_bg(lbl_cell, color)

        cnt_cell.paragraphs[0].text = str(count)
        cnt_cell.paragraphs[0].runs[0].font.size = Pt(16)
        cnt_cell.paragraphs[0].runs[0].bold = True
        cnt_cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)
        cnt_cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()

    # Tabela de dados
    headers = ["DATA", "CONDOMÍNIO", "SALÃO", "APTO.", "SOLICITANTE", "HORÁRIO", "VISTORIA", "STATUS"]
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.style = "Table Grid"

    STATUS_COLORS_WORD = {
        "confirmado": RGBColor(0x1A, 0x6A, 0x3A),
        "pendente":   RGBColor(0x7A, 0x4A, 0x00),
        "cancelado":  RGBColor(0x7A, 0x1A, 0x1A),
    }

    hdr_row = tbl.rows[0]
    for col_idx, hdr in enumerate(headers):
        cell = hdr_row.cells[col_idx]
        cell.paragraphs[0].text = hdr
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].runs[0].font.size = Pt(9)
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_cell_bg(cell, "1E3A5F")

    for i, r in enumerate(reservas):
        vistoria_str = r.data_vistoria.strftime("%d/%m/%Y") if r.vistoria and r.data_vistoria else "Mesmo dia"
        row_data = [
            r.data_festa.strftime("%d/%m/%Y"),
            r.salao.condominio.nome,
            r.salao.nome,
            r.apartamento or "[Condomínio]",
            r.nome_solicitante or "[Festa do Condomínio]",
            r.horario or "—",
            vistoria_str,
            r.status.capitalize(),
        ]
        row = tbl.add_row()
        bg = "EBF2FA" if i % 2 == 0 else "FFFFFF"
        for col_idx, text in enumerate(row_data):
            cell = row.cells[col_idx]
            p = cell.paragraphs[0]
            p.text = text
            p.runs[0].font.size = Pt(9)
            if col_idx in (0, 3, 5, 6, 7):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if col_idx == 7:
                p.runs[0].font.color.rgb = STATUS_COLORS_WORD.get(r.status, RGBColor(0, 0, 0))
                p.runs[0].bold = True
            set_cell_bg(cell, bg)
            set_cell_border(cell)

    # Rodapé
    doc.add_paragraph()
    footer_p = doc.add_paragraph(
        f"Gerado em {datetime.now(pytz.timezone('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M')} — CondoReservas Sistema de Reservas")
    footer_p.runs[0].font.size = Pt(8)
    footer_p.runs[0].font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
    footer_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


@relatorios_bp.route('/relatorio/semanal')
@login_required
@limiter.limit("10/minute")
def relatorio_semanal():
    hoje = get_hoje_br()
    default_inicio = hoje

    # Recupera a memória do último filtro usado pelo usuário
    ultimo_fim_str = session.get('relatorio_data_fim')
    if ultimo_fim_str:
        default_fim = date.fromisoformat(ultimo_fim_str)
        # Se a data salva já passou, reseta para o fim de semana atual (Domingo)
        if default_fim < hoje:
            default_fim = hoje + timedelta(days=6 - hoje.weekday())
    else:
        # Primeiro acesso: ancora no próximo domingo
        default_fim = hoje + timedelta(days=6 - hoje.weekday())

    try:
        data_inicio = date.fromisoformat(request.args.get('inicio', default_inicio.isoformat()))
        data_fim    = date.fromisoformat(request.args.get('fim',    default_fim.isoformat()))

        session['relatorio_data_fim'] = data_fim.isoformat()
    except ValueError:
        data_inicio, data_fim = default_inicio, default_fim

    reservas = (
        Reserva.query
        .join(Salao)
        .join(Condominio, Salao.condominio_id == Condominio.id)
        .options(
            joinedload(Reserva.salao).joinedload(Salao.condominio),
        )
        .filter(Reserva.data_festa >= data_inicio)
        .filter(Reserva.data_festa <= data_fim)
        .order_by(Condominio.nome, Reserva.data_festa)
        .all()
    )

    if request.args.get('pdf'):
        buffer = gerar_pdf_reservas(reservas, 'Reserva Salão de Festas', data_inicio, data_fim)
        registrar_documento('reservas', data_inicio)
        return send_file(buffer, mimetype='application/pdf',
                         download_name=f'reservas_{data_inicio}_{data_fim}.pdf',
                         as_attachment=False)

    if request.args.get('pdf_limpeza'):
        buffer = gerar_pdf_limpezas(reservas, data_inicio, data_fim)
        registrar_documento('limpezas', data_inicio)
        return send_file(buffer, mimetype='application/pdf',
                         download_name=f'limpezas_{data_inicio}_{data_fim}.pdf',
                         as_attachment=False)

    # [FEATURE] destaque de "boleto separado pendente" da semana vigente —
    # reserva cai na janela seg-dom de hoje, ainda não tem G marcado. Boleto
    # de condomínio NÃO destaca (lançado em lote na sexta, sem urgência);
    # só boleto separado é urgente porque sai fora desse lote.
    semana_atual_inicio = hoje - timedelta(days=hoje.weekday())
    semana_atual_fim    = semana_atual_inicio + timedelta(days=6)

    def _boleto_pendente(r):
        if r.status == 'cancelado' or r.festa_condominio or r.boleto_gerado:
            return False
        if not (semana_atual_inicio <= r.data_festa <= semana_atual_fim):
            return False
        if r.salao.valor == 0:
            return False
        if r.registro_anual and not r.registro_anual.valor_opcional_usado:
            return False
        forma = (r.salao.forma_pagamento or '').lower()
        return 'condomínio' not in forma

    # [FEATURE] cancelamento feito pelo fiscal no local (morador desiste na
    # hora da vistoria) — precisa aparecer destacado pro operador na segunda,
    # MESMO que a data da festa já tenha passado e caia fora do filtro
    # padrão (que começa em "hoje"). Por isso é uma busca à parte, não filtra
    # por data_inicio/data_fim: pega qualquer cancelamento dos últimos 7 dias
    # cujo cancelado_em (convertido pra horário de Brasília) caiu num
    # sábado/domingo.
    janela_fds = hoje - timedelta(days=7)
    canceladas_fds_raw = (
        Reserva.query
        .join(Salao)
        .options(joinedload(Reserva.salao).joinedload(Salao.condominio))
        .filter(Reserva.status == 'cancelado')
        .filter(Reserva.cancelado_em >= janela_fds)
        .all()
    )
    canceladas_fds_ids = {
        r.id for r in canceladas_fds_raw
        if r.cancelado_em_br and r.cancelado_em_br.weekday() in (5, 6)
    }

    if session.get('usuario_perfil') == 'fiscal':
        destaque_ids       = set()
        canceladas_fds_ids = set()
        reservas_view       = reservas
    else:
        destaque_ids   = {r.id for r in reservas if _boleto_pendente(r)}
        ids_no_periodo = {r.id for r in reservas}
        # Cancelamento fora da janela do filtro atual entra igual — senão
        # não aparece de jeito nenhum na segunda de manhã.
        extras = [r for r in canceladas_fds_raw if r.id in canceladas_fds_ids and r.id not in ids_no_periodo]
        reservas_combinadas = reservas + extras
        reservas_view = sorted(
            reservas_combinadas,
            key=lambda r: (r.id not in canceladas_fds_ids, r.id not in destaque_ids)
        )

    return render_template('relatorio.html',
        reservas=reservas_view, data_inicio=data_inicio, data_fim=data_fim,
        condominio=None, tipo='semanal', hoje=hoje,
        destaque_ids=destaque_ids, canceladas_fds_ids=canceladas_fds_ids)


@relatorios_bp.route('/relatorio/recibos-zelador')
@login_required
@limiter.limit("10/minute")
def recibos_zelador():
    # [TESTE] recibo de zelador automatizado — escolhe um período, lista as
    # reservas com zelador=True daquele período, gera um PDF em lote (um
    # recibo por reserva). Mesmo padrão de termos-comunicados/relatorio_fds.
    hoje = get_hoje_br()
    default_inicio = hoje - timedelta(days=hoje.weekday())  # segunda desta semana
    default_fim    = default_inicio + timedelta(days=6)

    try:
        data_inicio = date.fromisoformat(request.args.get('inicio', default_inicio.isoformat()))
        data_fim    = date.fromisoformat(request.args.get('fim', default_fim.isoformat()))
    except ValueError:
        data_inicio, data_fim = default_inicio, default_fim

    # [FIX 2026-07-30] reservas de condomínio zelador_pago_direto_fiscal
    # (Edifício Bela Vista, Residencial Palmeiras) não entram mais nem na listagem —
    # o recibo delas sai só junto do termo (ver gerar_pdf_termos /
    # story_reserva), não faz sentido aparecer aqui pra confundir.
    reservas = (
        Reserva.query.join(Salao).join(Condominio)
        .options(joinedload(Reserva.salao).joinedload(Salao.condominio))
        .filter(
            Reserva.data_festa >= data_inicio,
            Reserva.data_festa <= data_fim,
            Reserva.status != 'cancelado',
            Reserva.zelador == True,
            Condominio.zelador_pago_direto_fiscal == False,
        )
        .order_by(Reserva.data_festa, Condominio.nome)
        .all()
    )

    if request.args.get('pdf'):
        buffer = gerar_pdf_recibos_zelador(reservas, data_inicio, data_fim)
        registrar_documento('recibos_zelador', data_inicio)
        return send_file(buffer, mimetype='application/pdf',
                         download_name=f'recibos_zelador_{data_inicio}_{data_fim}.pdf',
                         as_attachment=False)

    return render_template('recibos_zelador.html',
        reservas=reservas, data_inicio=data_inicio, data_fim=data_fim)


@relatorios_bp.route('/relatorio/fim-de-semana')
@login_required
@limiter.limit("10/minute")
def relatorio_fds():
    hoje         = get_hoje_br()
    dias_ate_sab = (5 - hoje.weekday()) % 7 or 7
    default_ini  = hoje + timedelta(days=dias_ate_sab)
    default_fim  = default_ini + timedelta(days=1)

    inicio_str = request.args.get('inicio', default_ini.isoformat())
    fim_str    = request.args.get('fim',    default_fim.isoformat())

    try:
        data_inicio = date.fromisoformat(inicio_str)
        data_fim    = date.fromisoformat(fim_str)
    except ValueError:
        data_inicio, data_fim = default_ini, default_fim

    reservas = (
        Reserva.query
        .join(Salao).join(Condominio)
        .options(joinedload(Reserva.salao).joinedload(Salao.condominio))
        .filter(
            Reserva.data_vistoria >= data_inicio,
            Reserva.data_vistoria <= data_fim,
            Reserva.data_vistoria != None,
            Reserva.status != 'cancelado',
        )
        .order_by(Reserva.data_vistoria, Condominio.nome)
        .all()
    )

    # Mantém apenas reservas cuja data_vistoria cai no fim de semana (sáb/dom)
    # e cujo condomínio realmente precisa da Administradora nessa data. [FIX] usa
    # precisa_vistoria_em() em vez de só checar emite_termo — reserva criada
    # antes do condomínio marcar fiscal_proprio_seg_sab tem data_vistoria
    # congelado num sábado (fiscal próprio cobre, não é mais da Administradora); sem
    # reconsultar a regra atual, ela continuava aparecendo aqui como vistoria.
    reservas = [
        r for r in reservas
        if r.data_vistoria.weekday() in (5, 6)
        and r.salao.condominio.precisa_vistoria_em(r.data_vistoria)
    ]
    for r in reservas:
        r.dia_fds_efetivo = r.data_vistoria
        r.tipo_fds        = 'vistoria'

    # [TESTE] fiscal próprio (seg-sáb) — reserva cujo ÚNICO contato da Administradora é a
    # revistoria de domingo (a vistoria pré-festa é do fiscal próprio do
    # condomínio, nunca tem data_vistoria — a query acima nem pega essas
    # reservas). Sem isso, a festa de sábado desse tipo de condomínio nunca
    # aparecia neste relatório, mesmo precisando de revistoria da Administradora domingo.
    candidatas_revistoria = (
        Reserva.query
        .join(Salao).join(Condominio)
        .options(joinedload(Reserva.salao).joinedload(Salao.condominio))
        .filter(
            Reserva.data_vistoria.is_(None),
            Reserva.status != 'cancelado',
            Reserva.festa_condominio == False,
            Condominio.fiscal_proprio_seg_sab == True,
            Condominio.emite_termo == True,
            Reserva.data_festa >= data_inicio - timedelta(days=1),
            Reserva.data_festa <= data_fim,
        )
        .all()
    )
    for r in candidatas_revistoria:
        dia_rev = r.dia_revistoria
        if (dia_rev and data_inicio <= dia_rev <= data_fim and dia_rev.weekday() in (5, 6)
                and r.salao.condominio.precisa_vistoria_em(dia_rev)):
            r.dia_fds_efetivo = dia_rev
            r.tipo_fds        = 'revistoria'
            reservas.append(r)

    reservas.sort(key=lambda r: (r.dia_fds_efetivo, r.salao.condominio.nome))

    if request.args.get('excel'):
        buffer = gerar_excel_fds(reservas, data_inicio, data_fim)
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'fds_{data_inicio}_{data_fim}.xlsx'
        )

    return render_template('fds.html',
        reservas    = reservas,
        data_inicio = data_inicio,
        data_fim    = data_fim,
        timedelta   = timedelta,
    )

@relatorios_bp.route('/relatorio/condominio/<int:condominio_id>')
@login_required
@limiter.limit("10/minute")
def relatorio_condominio(condominio_id):
    import calendar
    condominio  = Condominio.query.get_or_404(condominio_id)
    hoje        = get_hoje_br()

    data_inicio_str = request.args.get('inicio')
    data_fim_str    = request.args.get('fim')
    apartamento     = request.args.get('apartamento', '').strip()

    if data_inicio_str and data_fim_str:
        try:
            primeiro_dia = date.fromisoformat(data_inicio_str)
            ultimo_dia   = date.fromisoformat(data_fim_str)
        except ValueError:
            primeiro_dia = date(hoje.year, hoje.month, 1)
            ultimo_dia   = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    else:
        mes          = request.args.get('mes', hoje.month, type=int)
        ano          = request.args.get('ano', hoje.year,  type=int)
        if not (1 <= mes <= 12 and 2000 <= ano <= 2100):
            mes, ano = hoje.month, hoje.year
        primeiro_dia = date(ano, mes, 1)
        ultimo_dia   = date(ano, mes, calendar.monthrange(ano, mes)[1])

    query = (
        Reserva.query
        .join(Salao)
        .options(
            joinedload(Reserva.salao).joinedload(Salao.condominio),
        )
        .filter(Salao.condominio_id == condominio_id)
        .filter(Reserva.data_festa >= primeiro_dia)
        .filter(Reserva.data_festa <= ultimo_dia)
    )

    reservas = query.order_by(Reserva.data_festa).all()

    # [FIX] apartamento é ciphertext no banco (_apartamento_enc) — nunca dá
    # pra filtrar com .ilike() direto no SQLAlchemy (ver database-rules.md).
    # O filtro estava sendo aplicado na query acima e sempre voltava vazio
    # silenciosamente. Filtra em memória, comparando o valor já decriptado.
    if apartamento:
        apt_busca = apartamento.strip().lower()
        reservas = [r for r in reservas if r.apartamento and apt_busca in r.apartamento.lower()]

    if request.args.get('pdf'):
        buffer = gerar_pdf_reservas(reservas, 'Reserva Salão de Festas', primeiro_dia, ultimo_dia)
        return send_file(buffer, mimetype='application/pdf',
                         download_name=f'reservas_{primeiro_dia}_{ultimo_dia}.pdf',
                         as_attachment=False)

    if request.args.get('pdf_limpeza'):
        buffer = gerar_pdf_limpezas(reservas, primeiro_dia, ultimo_dia)
        return send_file(buffer, mimetype='application/pdf',
                         download_name=f'limpezas_{primeiro_dia}_{ultimo_dia}.pdf',
                         as_attachment=False)

    if request.args.get('excel'):
        buffer = gerar_excel_reservas(reservas, f'Relatório — {condominio.nome}', primeiro_dia, ultimo_dia)
        return send_file(buffer,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         download_name=f'reservas_{primeiro_dia}_{ultimo_dia}.xlsx')

    if request.args.get('word'):
        buffer = gerar_word_reservas(reservas, f'Relatório — {condominio.nome}', primeiro_dia, ultimo_dia)
        return send_file(buffer,
                         mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                         download_name=f'reservas_{primeiro_dia}_{ultimo_dia}.docx')

    return render_template('relatorio.html',
        reservas    = reservas,
        data_inicio = primeiro_dia,
        data_fim    = ultimo_dia,
        condominio  = condominio,
        tipo        = 'mensal',
        apartamento = apartamento,
        timedelta   = timedelta,
        hoje        = hoje,
    )


@relatorios_bp.route('/admin/feriados')
@login_required
def admin_feriados():
    feriados = Feriado.query.order_by(Feriado.mes, Feriado.dia).all()
    return render_template('admin_feriados.html', feriados=feriados)


@relatorios_bp.route('/admin/feriados/novo', methods=['POST'])
@login_required
def novo_feriado():
    try:
        dia = int(request.form['dia'])
        mes = int(request.form['mes'])
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            raise ValueError
    except (ValueError, TypeError):
        flash('Dia ou mês inválido.', 'danger')
        return redirect(url_for('relatorios.admin_feriados'))
    ano_raw = request.form.get('ano', '').strip() or None
    if ano_raw is not None:
        try:
            ano_val = int(ano_raw)
            if not (2020 <= ano_val <= 2099):
                raise ValueError
            ano_raw = ano_val
        except (ValueError, TypeError):
            flash('Ano inválido (use um valor entre 2020 e 2099).', 'danger')
            return redirect(url_for('relatorios.admin_feriados'))
    nome_raw = sanitize_text(request.form.get('nome', ''), 100)
    if not nome_raw:
        flash('Nome do feriado é obrigatório.', 'danger')
        return redirect(url_for('relatorios.admin_feriados'))
    feriado = Feriado(
        nome = nome_raw,
        dia  = dia,
        mes  = mes,
        ano  = ano_raw
    )
    try:
        db.session.add(feriado)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('Erro ao salvar feriado.', 'danger')
        return redirect(url_for('relatorios.admin_feriados'))
    log_audit(
        session.get('usuario_id'), 'criar_feriado', 'feriados', feriado.id,
        dados_depois={'nome': feriado.nome, 'dia': feriado.dia, 'mes': feriado.mes}
    )
    flash('Feriado adicionado com sucesso.', 'success')
    return redirect(url_for('relatorios.admin_feriados'))


@relatorios_bp.route('/admin/feriados/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_feriado(id):
    feriado = Feriado.query.get_or_404(id)
    try:
        db.session.delete(feriado)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('Erro ao excluir feriado.', 'danger')
    return redirect(url_for('relatorios.admin_feriados'))


@relatorios_bp.route('/admin/bloqueios', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_bloqueios():
    condominios = Condominio.query.order_by(Condominio.nome).all()
    if request.method == 'POST':
        try:
            data_inicio = date.fromisoformat(request.form['data_inicio'])
            data_fim    = date.fromisoformat(request.form['data_fim'])
        except (ValueError, TypeError):
            flash('Datas inválidas.', 'danger')
            bloqueios = Bloqueio.query.order_by(Bloqueio.data_inicio).all()
            return render_template('admin_bloqueios.html', bloqueios=bloqueios, condominios=condominios)
        if data_inicio > data_fim:
            flash('Data de início deve ser anterior ou igual à data de fim.', 'danger')
            bloqueios = Bloqueio.query.order_by(Bloqueio.data_inicio).all()
            return render_template('admin_bloqueios.html', bloqueios=bloqueios, condominios=condominios)
        descricao_raw = sanitize_text(request.form.get('descricao', ''), 200)
        if not descricao_raw:
            flash('Descrição do bloqueio é obrigatória.', 'danger')
            bloqueios = Bloqueio.query.order_by(Bloqueio.data_inicio).all()
            return render_template('admin_bloqueios.html', bloqueios=bloqueios, condominios=condominios)
        bloqueio = Bloqueio(
            data_inicio   = data_inicio,
            data_fim      = data_fim,
            descricao     = descricao_raw,
            condominio_id = request.form.get('condominio_id') or None
        )
        try:
            db.session.add(bloqueio)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar bloqueio.', 'danger')
            return redirect(url_for('relatorios.admin_bloqueios'))
        log_audit(
            session.get('usuario_id'), 'criar_bloqueio', 'bloqueios', bloqueio.id,
            dados_depois={
                'data_inicio': bloqueio.data_inicio.isoformat(),
                'data_fim':    bloqueio.data_fim.isoformat(),
                'descricao':   bloqueio.descricao,
            }
        )
        return redirect(url_for('relatorios.admin_bloqueios'))
    bloqueios = Bloqueio.query.order_by(Bloqueio.data_inicio).all()
    return render_template('admin_bloqueios.html', bloqueios=bloqueios, condominios=condominios)


@relatorios_bp.route('/admin/bloqueios/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_bloqueio(id):
    bloqueio = Bloqueio.query.get_or_404(id)
    try:
        db.session.delete(bloqueio)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('Erro ao excluir bloqueio.', 'danger')
    return redirect(url_for('relatorios.admin_bloqueios'))


@relatorios_bp.route('/admin/pendentes')
@login_required
def admin_pendentes():
    pendentes = (
        Reserva.query.join(Salao).join(Condominio)
        .filter(Reserva.status == 'pendente')
        .filter(Reserva.data_festa >= get_hoje_br())
        .order_by(Reserva.data_festa)
        .all()
    )
    return render_template('admin_pendentes.html', pendentes=pendentes)


@relatorios_bp.route('/admin/historico')
@login_required
def admin_historico():
    from app.models import Historico
    historicos = (
        Historico.query.join(Reserva).join(Salao).join(Condominio)
        .order_by(Historico.criado_em.desc())
        .limit(200)
        .all()
    )
    return render_template('admin_historico.html', historicos=historicos)

_APT_RE_CRED = __import__('re').compile(r'^[0-9]{2,3}[A-Z0-9\-]*$')

@relatorios_bp.route('/admin/creditos', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_creditos():
    from app.models import Credito
    from decimal import Decimal, InvalidOperation
    condominios = Condominio.query.order_by(Condominio.nome).all()
    if request.method == 'POST':
        try:
            condominio_id = int(request.form['condominio_id'])
            salao_id      = int(request.form['salao_id'])
        except (ValueError, TypeError):
            flash('Dados inválidos. Verifique os campos.', 'danger')
            return redirect(url_for('relatorios.admin_creditos'))
        try:
            valor = Decimal(str(request.form.get('valor', '')))
            if not (Decimal('0.00') <= valor <= Decimal('99999.99')):
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
            flash('Valor inválido (mínimo R$ 0,00, máximo R$ 99.999,99).', 'danger')
            return redirect(url_for('relatorios.admin_creditos'))
        apt = request.form.get('apartamento', '').strip()
        if not apt or not _APT_RE_CRED.match(apt.upper()) or len(apt) > 20:
            flash('Apartamento inválido (ex: 401, 704-M, 602-2, 301A).', 'danger')
            return redirect(url_for('relatorios.admin_creditos'))
        Condominio.query.get_or_404(condominio_id)
        obs = request.form.get('observacao', '').strip()
        credito = Credito(
            condominio_id = condominio_id,
            salao_id      = salao_id,
            apartamento   = apt,
            valor         = float(valor),
            observacao    = obs[:200] if obs else None,
            usado         = False
        )
        try:
            db.session.add(credito)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar crédito.', 'danger')
            return redirect(url_for('relatorios.admin_creditos'))
        log_audit(
            session.get('usuario_id'), 'criar_credito', 'creditos', credito.id,
            dados_depois={'condominio_id': credito.condominio_id, 'valor': float(credito.valor)}
        )
        return redirect(url_for('relatorios.admin_creditos'))

    creditos = (Credito.query.join(Salao).join(Condominio)
                .filter(Credito.usado == False)
                .order_by(Condominio.nome, Credito.apartamento)
                .all())
    return render_template('admin_creditos.html', condominios=condominios, creditos=creditos)


@relatorios_bp.route('/admin/creditos/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_credito(id):
    from app.models import Credito
    credito = Credito.query.get_or_404(id)
    try:
        db.session.delete(credito)
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash('Erro ao excluir crédito.', 'danger')
    return redirect(url_for('relatorios.admin_creditos'))


@relatorios_bp.route('/admin/inventario/<int:salao_id>', methods=['GET', 'POST'])
@login_required
def admin_inventario(salao_id):
    salao = Salao.query.get_or_404(salao_id)

    if request.method == 'POST':
        acao = request.form.get('acao', 'salvar_itens')

        # ── Salvar lista de itens (comportamento original) ───────────────────
        try:
            # [FIX] antes apagava TUDO e recriava do zero a cada save — quebra
            # com ForeignKeyViolation assim que qualquer item já foi usado
            # numa vistoria digital (VistoriaItem referencia o id antigo por
            # FK). Agora casa por descrição: item que continua na lista
            # reaproveita o registro existente (mesmo id, sem mexer no
            # histórico); item removido da lista só é apagado de verdade se
            # nunca foi usado — senão vira ativo=False (soft delete).
            existentes = ItemInventario.query.filter_by(salao_id=salao_id).all()
            por_descricao = {}
            for it in existentes:
                por_descricao.setdefault(it.descricao.strip(), []).append(it)

            descricoes_submetidas = [
                str(d).strip()[:200] for d in request.form.getlist('item')
                if d and str(d).strip()
            ]

            usados_ids = set()
            for i, descricao in enumerate(descricoes_submetidas):
                candidatos = por_descricao.get(descricao)
                if candidatos:
                    item = candidatos.pop(0)
                    item.ordem = i
                    item.ativo = True
                    usados_ids.add(item.id)
                else:
                    db.session.add(ItemInventario(
                        salao_id=salao_id, descricao=descricao, ordem=i, ativo=True
                    ))

            for it in existentes:
                if it.id in usados_ids:
                    continue
                tem_historico = VistoriaItem.query.filter_by(item_inventario_id=it.id).first() is not None
                if tem_historico:
                    it.ativo = False
                else:
                    db.session.delete(it)

            db.session.commit()
            flash('Inventário salvo com sucesso.', 'success')
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"ERRO FATAL NO INVENTÁRIO: {str(e)}")
            db.session.rollback()
            flash('Erro ao salvar inventário. Tente novamente.', 'danger')
        return redirect(url_for('relatorios.admin_inventario', salao_id=salao_id))

    itens    = ItemInventario.query.filter_by(salao_id=salao_id, ativo=True).order_by(ItemInventario.ordem).all()
    problemas= ItemProblema.query.filter_by(salao_id=salao_id).order_by(ItemProblema.registrado_em.desc()).all()
    return render_template('admin_inventario.html', salao=salao, itens=itens, problemas=problemas)


@relatorios_bp.route('/admin/inventario/<int:salao_id>/problema/<int:problema_id>/resolver', methods=['POST'])
@login_required
def resolver_problema(salao_id, problema_id):
    # [TESTE] problema persistente — admin/operador também pode resolver aqui
    # (o fiscal resolve direto na vistoria, apagando o texto da obs).
    problema = ItemProblema.query.filter_by(id=problema_id, salao_id=salao_id).first_or_404()
    try:
        db.session.delete(problema)
        db.session.commit()
        flash('Problema marcado como resolvido.', 'success')
    except Exception:
        db.session.rollback()
        flash('Erro ao marcar como resolvido.', 'danger')
    return redirect(url_for('relatorios.admin_inventario', salao_id=salao_id))


@relatorios_bp.route('/vistoria/<int:reserva_id>/fotos')
@login_required
def ver_fotos_vistoria(reserva_id):
    # [TESTE] galeria simples das fotos de problema anexadas nos termos dessa
    # reserva (vistoria e/ou revistoria) — imagem individual servida por
    # vistorias.ver_foto.
    reserva = Reserva.query.get_or_404(reserva_id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    termos  = VistoriaTermo.query.filter_by(reserva_id=reserva_id).all()
    fotos   = [f for t in termos for f in t.fotos]
    return render_template('vistoria_fotos.html', reserva=reserva, fotos=fotos)


@relatorios_bp.route('/admin/regras/<int:condominio_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_regras(condominio_id):
    from app.models import RegraRegimento
    cond  = Condominio.query.get_or_404(condominio_id)
    regra = RegraRegimento.query.filter_by(condominio_id=condominio_id).first()
    if request.method == 'POST':
        # [FIX] texto ia direto pro banco sem sanitize_text — único campo
        # de texto livre do sistema que fugia dessa regra. Reflete tanto no
        # HTML do painel_fiscal/vistoria_form quanto como texto puro dentro
        # de Paragraph do ReportLab (que interpreta <tags> como markup).
        texto_sanitizado = sanitize_text(request.form.get('texto', ''), 4000)
        if regra:
            regra.texto = texto_sanitizado
        else:
            db.session.add(RegraRegimento(condominio_id=condominio_id, texto=texto_sanitizado))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar regras.', 'danger')
            return render_template('admin_regras.html', cond=cond, regra=regra)
        return redirect(url_for('condominios.ver_condominio', id=condominio_id))
    return render_template('admin_regras.html', cond=cond, regra=regra)


@relatorios_bp.route('/termos-comunicados')
@login_required
def termos_comunicados():
    hoje = get_hoje_br()
    default_inicio = hoje

    # Recupera a memória do último filtro usado pelo usuário
    ultimo_fim_str = session.get('relatorio_data_fim')
    if ultimo_fim_str:
        default_fim = date.fromisoformat(ultimo_fim_str)
        # Se a data salva já passou, reseta para o fim de semana atual (Domingo)
        if default_fim < hoje:
            default_fim = hoje + timedelta(days=6 - hoje.weekday())
    else:
        # Primeiro acesso: ancora no próximo domingo
        default_fim = hoje + timedelta(days=6 - hoje.weekday())

    try:
        data_inicio = date.fromisoformat(request.args.get('inicio', default_inicio.isoformat()))
        data_fim    = date.fromisoformat(request.args.get('fim',    default_fim.isoformat()))

        session['relatorio_data_fim'] = data_fim.isoformat()
    except ValueError:
        data_inicio, data_fim = default_inicio, default_fim
    apartamento   = request.args.get('apartamento', '').strip()
    condominio_id = request.args.get('condominio_id', '', type=str)
    apenas_novos  = request.args.get('apenas_novos', '0')

    query = Reserva.query.join(Salao).join(Condominio) \
        .options(
            joinedload(Reserva.salao).joinedload(Salao.condominio),
            # [FEATURE] pedido do Viny: marcação de vistoria/revistoria
            # feita saiu do relatório semanal e passou pra cá — precisa do
            # mesmo eager load que o relatorio.html usava, senão cada linha
            # dispara lazy query própria pra vistorias_termo.
            selectinload(Reserva.vistorias_termo),
        ) \
        .filter(Reserva.data_festa >= data_inicio) \
        .filter(Reserva.data_festa <= data_fim) \
        .filter(Reserva.status != 'cancelado') \
        .filter(db.or_(Condominio.emite_termo == True, Condominio.emite_comunicado == True))

    if condominio_id:
        try:
            query = query.filter(Salao.condominio_id == int(condominio_id))
        except (ValueError, TypeError):
            condominio_id = ''

    reservas    = query.order_by(Reserva.data_festa).all()
    condominios = Condominio.query.order_by(Condominio.nome).all()

    # [FIX] apartamento é ciphertext no banco — nunca dá pra filtrar com
    # .ilike() direto no SQLAlchemy (ver database-rules.md). Filtro em
    # memória, comparando o valor já decriptado.
    if apartamento:
        apt_busca = apartamento.strip().lower()
        reservas = [r for r in reservas if r.apartamento and apt_busca in r.apartamento.lower()]

    # [FEATURE] vistoria antecipada (D-1) — a reserva precisa aparecer
    # agrupada no dia em que o fiscal efetivamente vai lá (data_vistoria),
    # não no dia da festa. Sem isso a vistoria de hoje some da lista de hoje
    # e só aparece "escondida" junto da festa de amanhã.
    def _data_exibicao(r):
        if r.data_vistoria and r.data_vistoria < r.data_festa:
            return r.data_vistoria
        return r.data_festa

    antecipada_ids = {
        r.id for r in reservas
        if r.data_vistoria and r.data_vistoria < r.data_festa
    }
    reservas = sorted(reservas, key=_data_exibicao)

    def ja_impresso(reserva_id, tipo):
        return DocumentoGerado.query.filter_by(
            reserva_id=reserva_id, tipo=tipo
        ).first() is not None

    if request.args.get('pdf_comunicados'):
        try:
            if apenas_novos == '1':
                # "Apenas novos" seleciona CONDOMÍNIOS que têm reserva nova
                # (ainda sem comunicado impresso) — mas o comunicado de um
                # condomínio tem que sair completo, com todas as reservas do
                # período, não só a reserva nova. Do contrário a festa antiga
                # já comunicada desaparece do lote quando uma festa nova do
                # mesmo condomínio entra na janela.
                novas = [r for r in reservas if not ja_impresso(r.id, 'comunicados')]
                conds_com_novo = {r.salao.condominio_id for r in novas}
                reservas_com = [r for r in reservas if r.salao.condominio_id in conds_com_novo]
            else:
                novas = reservas
                reservas_com = reservas
            for r in novas:
                db.session.add(DocumentoGerado(
                    tipo=          'comunicados',
                    reserva_id=    r.id,
                    condominio_id= r.salao.condominio_id,
                    semana_inicio= data_inicio,
                    gerado_em=     datetime.now(timezone.utc)
                ))
            db.session.commit()
            buffer = gerar_pdf_comunicados(reservas_com, data_inicio, data_fim)
            return send_file(buffer, mimetype='application/pdf',
                             download_name=f'comunicados_{data_inicio}_{data_fim}.pdf',
                             as_attachment=False)
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erro ao gerar PDF comunicados: {e}")
            flash('Erro ao gerar o PDF de comunicados. Tente novamente.', 'error')
            return redirect(url_for(request.endpoint, **request.view_args))

    if request.args.get('pdf_termos'):
        try:
            if apenas_novos == '1':
                reservas_termo = [r for r in reservas if not ja_impresso(r.id, 'termos')]
            else:
                reservas_termo = reservas
            for r in reservas_termo:
                db.session.add(DocumentoGerado(
                    tipo=          'termos',
                    reserva_id=    r.id,
                    condominio_id= r.salao.condominio_id,
                    semana_inicio= data_inicio,
                    gerado_em=     datetime.now(timezone.utc)
                ))
            db.session.commit()
            buffer = gerar_pdf_termos(reservas_termo, data_inicio, data_fim)
            return send_file(buffer, mimetype='application/pdf',
                             download_name=f'termos_{data_inicio}_{data_fim}.pdf',
                             as_attachment=False)
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erro ao gerar PDF termos: {e}")
            flash('Erro ao gerar o PDF de termos. Tente novamente.', 'error')
            return redirect(url_for(request.endpoint, **request.view_args))

    janela = data_inicio - timedelta(days=30)
    ids_com_termo = {
        d.reserva_id for d in
        DocumentoGerado.query
        .filter_by(tipo='termos')
        .filter(DocumentoGerado.semana_inicio >= janela)
        .all()
    }
    ids_com_comunicado = {
        d.reserva_id for d in
        DocumentoGerado.query
        .filter_by(tipo='comunicados')
        .filter(DocumentoGerado.semana_inicio >= janela)
        .all()
    }

    return render_template('termos_comunicados.html',
        reservas=reservas, data_inicio=data_inicio, data_fim=data_fim,
        condominios=condominios, apartamento=apartamento,
        condominio_id=condominio_id, apenas_novos=apenas_novos,
        ids_com_termo=ids_com_termo, ids_com_comunicado=ids_com_comunicado,
        antecipada_ids=antecipada_ids, hoje=hoje)

@relatorios_bp.route('/termo/<int:reserva_id>/pdf')
@login_required
@limiter.limit("10/minute")
def termo_individual(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    try:
        db.session.add(DocumentoGerado(
            tipo=          'termos',
            reserva_id=    reserva.id,
            condominio_id= reserva.salao.condominio_id,
            semana_inicio= reserva.data_festa,
            gerado_em=     datetime.now(timezone.utc)
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
    buffer = gerar_pdf_termos([reserva], reserva.data_festa, reserva.data_festa)
    return send_file(buffer, mimetype='application/pdf',
                     download_name=f'termo_{reserva.apartamento}_{reserva.data_festa}.pdf',
                     as_attachment=False)


@relatorios_bp.route('/comunicado/<int:reserva_id>/pdf')
@login_required
def comunicado_individual(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    try:
        db.session.add(DocumentoGerado(
            tipo=          'comunicados',
            reserva_id=    reserva.id,
            condominio_id= reserva.salao.condominio_id,
            semana_inicio= reserva.data_festa,
            gerado_em=     datetime.now(timezone.utc)
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
    buffer = gerar_pdf_comunicados([reserva], reserva.data_festa, reserva.data_festa)
    return send_file(buffer, mimetype='application/pdf',
                     download_name=f'comunicado_{reserva.apartamento}_{reserva.data_festa}.pdf',
                     as_attachment=False)


@relatorios_bp.route('/admin/regimento/<int:condominio_id>', methods=['POST'])
@login_required
@admin_required
def upload_regimento(condominio_id):
    cond = Condominio.query.get_or_404(condominio_id)
    arquivo = request.files.get('regimento')
    if arquivo and arquivo.filename.lower().endswith('.pdf'):
        header = arquivo.read(4)
        arquivo.seek(0)
        if header != b'%PDF':
            flash('Arquivo inválido. Envie um PDF real.', 'error')
            return redirect(url_for('condominios.ver_condominio', id=condominio_id))
        dados_pdf = arquivo.read()
        cond.regimento_pdf_data = dados_pdf
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar regimento.', 'error')
            return redirect(url_for('condominios.ver_condominio', id=condominio_id))
        log_audit(
            session.get('usuario_id'), 'upload_regimento', 'condominios', condominio_id,
            dados_depois={'arquivo': arquivo.filename}
        )
        flash('Regimento anexado com sucesso.', 'success')
    else:
        flash('Envie um arquivo PDF válido.', 'error')
    return redirect(url_for('condominios.ver_condominio', id=condominio_id))


@relatorios_bp.route('/admin/audit-log')
@login_required
@admin_required
def admin_audit_log():
    page           = request.args.get('page', 1, type=int)
    acao_filtro    = request.args.get('acao', '').strip()
    data_inicio_s  = request.args.get('data_inicio', '').strip()
    data_fim_s     = request.args.get('data_fim', '').strip()

    query = AuditLog.query.order_by(AuditLog.criado_em.desc())

    if acao_filtro:
        query = query.filter(AuditLog.acao == acao_filtro)
    if data_inicio_s:
        try:
            query = query.filter(AuditLog.criado_em >= datetime.fromisoformat(data_inicio_s))
        except ValueError:
            pass
    if data_fim_s:
        try:
            query = query.filter(AuditLog.criado_em <= datetime.fromisoformat(data_fim_s + 'T23:59:59'))
        except ValueError:
            pass

    if request.args.get('csv'):
        entries = query.limit(1000).all()
        buf = []
        buf.append('ID,Usuário,Ação,Tabela,Registro ID,Data,IP,Dados Antes,Dados Depois\n')
        for e in entries:
            row = [
                str(e.id),
                (e.usuario.nome if e.usuario else 'sistema').replace('"', '""'),
                e.acao,
                e.tabela or '',
                str(e.registro_id or ''),
                e.criado_em.strftime('%d/%m/%Y %H:%M'),
                e.ip_address or '',
                json.dumps(e.dados_antes,  ensure_ascii=False) if e.dados_antes  else '',
                json.dumps(e.dados_depois, ensure_ascii=False) if e.dados_depois else '',
            ]
            buf.append(','.join(f'"{c}"' for c in row) + '\n')
        return Response(
            ''.join(buf),
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename=audit_log_{get_hoje_br()}.csv'}
        )

    pagination = query.paginate(page=page, per_page=100, error_out=False)
    entries    = pagination.items

    acoes = [r[0] for r in db.session.query(AuditLog.acao).distinct().order_by(AuditLog.acao).all()]

    for e in entries:
        e._antes_fmt  = json.dumps(e.dados_antes,  indent=2, ensure_ascii=False) if e.dados_antes  else None
        e._depois_fmt = json.dumps(e.dados_depois, indent=2, ensure_ascii=False) if e.dados_depois else None

    return render_template('admin_audit_log.html',
        entries       = entries,
        pagination    = pagination,
        acoes         = acoes,
        acao_filtro   = acao_filtro,
        data_inicio_s = data_inicio_s,
        data_fim_s    = data_fim_s,
    )


@relatorios_bp.route('/admin/deletar-dados-pessoais/<int:reserva_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def deletar_dados_pessoais(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    if request.method == 'POST':
        confirmacao  = request.form.get('confirmacao', '').strip()
        id_digitado  = request.form.get('reserva_id_confirmar', '').strip()
        aceite       = request.form.get('aceite')
        if confirmacao != 'DELETAR' or id_digitado != str(reserva_id) or not aceite:
            flash('Confirmação incorreta. Operação cancelada.', 'error')
            return redirect(url_for('relatorios.deletar_dados_pessoais', reserva_id=reserva_id))
        reserva._nome_solicitante_enc = None
        reserva._contato_enc          = None
        reserva._apartamento_enc      = None
        reserva.registrado_por        = None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao deletar dados pessoais.', 'error')
            return redirect(url_for('relatorios.deletar_dados_pessoais', reserva_id=reserva_id))

        # Limpeza retroativa: criar_reserva/editar_reserva gravavam
        # nome_solicitante/apartamento decriptados em audit_logs.dados_antes
        # e dados_depois — a exclusão acima só zera a reserva, então sem isso
        # a PII "excluída" continuava recuperável em texto plano no log.
        def _redigir(campos):
            if not campos:
                return campos, False
            novo = dict(campos)
            mudou = False
            for campo in ('nome_solicitante', 'apartamento'):
                if campo in novo and novo[campo] not in (None, '[alterado]', '[dados removidos - LGPD]'):
                    novo[campo] = '[dados removidos - LGPD]'
                    mudou = True
            return novo, mudou

        try:
            logs_antigos = AuditLog.query.filter_by(tabela='reservas', registro_id=reserva.id).all()
            for entry in logs_antigos:
                novo_antes, mudou_antes   = _redigir(entry.dados_antes)
                novo_depois, mudou_depois = _redigir(entry.dados_depois)
                if mudou_antes:
                    entry.dados_antes = novo_antes
                if mudou_depois:
                    entry.dados_depois = novo_depois
            db.session.commit()
        except Exception:
            db.session.rollback()
            logging.error(f"Erro ao redigir PII retroativa no audit log da reserva {reserva.id}")

        log_audit(
            session.get('usuario_id'), 'deletar_dados_pessoais', 'reservas', reserva.id,
            dados_depois={'acao': 'dados_pessoais_deletados', 'reserva_id': reserva.id}
        )
        flash('Dados pessoais deletados conforme LGPD.', 'success')
        return redirect(url_for('relatorios.admin_deletadas_pessoais'))
    return render_template('admin_deletar_dados.html', reserva=reserva)


@relatorios_bp.route('/admin/deletadas-pessoais')
@login_required
@admin_required
def admin_deletadas_pessoais():
    reservas = (
        Reserva.query
        .filter(Reserva._nome_solicitante_enc == None)
        .order_by(Reserva.data_festa.desc())
        .all()
    )
    ids = [r.id for r in reservas]
    audit_map = {}
    if ids:
        entries = (
            AuditLog.query
            .filter(AuditLog.acao == 'deletar_dados_pessoais', AuditLog.registro_id.in_(ids))
            .order_by(AuditLog.criado_em.desc())
            .all()
        )
        for e in entries:
            if e.registro_id not in audit_map:
                audit_map[e.registro_id] = e
    return render_template('admin_deletadas_pessoais.html',
        reservas=reservas, audit_map=audit_map, total=len(reservas))


@relatorios_bp.route('/admin/regras-precificacao')
@login_required
@admin_required
def listar_regras_precificacao():
    regras = RegraPrecificacao.query.join(Salao).order_by(Salao.nome).all()
    return render_template('admin_regras_precificacao.html', regras=regras)


@relatorios_bp.route('/admin/regras-precificacao/nova', methods=['GET', 'POST'])
@login_required
@admin_required
def nova_regra_precificacao():
    if request.method == 'POST':
        try:
            salao_id       = int(request.form['salao_id'])
            limite         = int(request.form.get('limite_reservas', 1))
            valor_opcional = request.form.get('valor_opcional', '').strip()
            descricao      = sanitize_text(request.form.get('descricao', ''), 200)

            existente = RegraPrecificacao.query.filter_by(salao_id=salao_id).first()
            if existente:
                flash('Já existe uma regra para este salão. Edite a existente.', 'danger')
                return redirect(url_for('relatorios.listar_regras_precificacao'))

            regra = RegraPrecificacao(
                salao_id        = salao_id,
                limite_reservas = max(1, limite),
                valor_opcional  = float(valor_opcional) if valor_opcional else None,
                descricao       = descricao,
                ativo           = True,
            )
            db.session.add(regra)
            db.session.commit()
            flash('Regra criada com sucesso.', 'success')
        except Exception as e:
            db.session.rollback()
            logging.error(f'Erro ao criar regra_precificacao: {e}')
            flash('Erro ao salvar regra.', 'danger')
        return redirect(url_for('relatorios.listar_regras_precificacao'))

    condominios = Condominio.query.options(joinedload(Condominio.saloes)).order_by(Condominio.nome).all()
    return render_template('admin_regras_precificacao.html', condominios=condominios, novo=True)


@relatorios_bp.route('/admin/regras-precificacao/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_regra_precificacao(id):
    regra = RegraPrecificacao.query.get_or_404(id)
    if request.method == 'POST':
        try:
            limite         = int(request.form.get('limite_reservas', 1))
            valor_opcional = request.form.get('valor_opcional', '').strip()
            regra.limite_reservas = max(1, limite)
            regra.valor_opcional  = float(valor_opcional) if valor_opcional else None
            regra.descricao       = sanitize_text(request.form.get('descricao', ''), 200)
            regra.ativo           = 'ativo' in request.form
            db.session.commit()
            flash('Regra atualizada com sucesso.', 'success')
        except Exception as e:
            db.session.rollback()
            logging.error(f'Erro ao editar regra_precificacao {id}: {e}')
            flash('Erro ao salvar alterações.', 'danger')
            return render_template('admin_regras_precificacao.html', editar=regra, ano_atual=date.today().year)
        return redirect(url_for('relatorios.listar_regras_precificacao'))

    return render_template('admin_regras_precificacao.html', editar=regra, ano_atual=date.today().year)


@relatorios_bp.route('/admin/regras-precificacao/<int:id>/aplicar-retroativo', methods=['POST'])
@login_required
@admin_required
def aplicar_retroativo_regra_precificacao(id):
    # [FIX] a isenção só é calculada no momento em que a reserva é criada —
    # regra cadastrada depois nunca alcança reservas já existentes. Essa ação
    # varre as reservas do salão neste ano (sem registro ainda) e aplica a
    # regra retroativamente, respeitando o limite por apartamento, na ordem
    # cronológica (a mais antiga do apto usa a cota primeiro — mesma lógica
    # de "1ª reserva do ano" que já vale pra reservas novas).
    from app.models import Historico
    regra = RegraPrecificacao.query.get_or_404(id)
    usuario = session.get('usuario_login', session.get('usuario_nome', ''))
    ano_atual = date.today().year
    usar_opcional = 'usar_opcional' in request.form and regra.valor_opcional is not None

    try:
        condominio_id = regra.salao.condominio_id
        reservas = (
            Reserva.query
            .filter(
                Reserva.salao_id == regra.salao_id,
                Reserva.status != 'cancelado',
                Reserva.festa_condominio == False,
                Reserva.data_festa >= date(ano_atual, 1, 1),
                Reserva.data_festa <= date(ano_atual, 12, 31),
            )
            .order_by(Reserva.data_festa.asc())
            .all()
        )

        contagem_por_apto = {}
        aplicadas = 0
        for r in reservas:
            apto = (r.apartamento or '').strip().upper()
            if not apto:
                continue
            usado = contagem_por_apto.get(apto, 0)
            if r.registro_anual:
                # já tem registro (de quando foi criada, ou de uma aplicação
                # retroativa anterior) — não sobrescreve, só conta pra ordem.
                contagem_por_apto[apto] = usado + 1
                continue
            if usado < regra.limite_reservas:
                db.session.add(ReservaAnualUnidade(
                    condominio_id        = condominio_id,
                    salao_id             = regra.salao_id,
                    apartamento          = apto,
                    ano                  = ano_atual,
                    reserva_id           = r.id,
                    valor_opcional_usado = usar_opcional,
                ))
                desc = (
                    f'Isenção aplicada retroativamente — {regra.descricao or "Regra especial"} '
                    f'({usado + 1}ª/{regra.limite_reservas} do ano {ano_atual})'
                )
                if usar_opcional:
                    desc += f' | Adicional: R$ {regra.valor_opcional:.2f}'
                db.session.add(Historico(reserva_id=r.id, descricao=desc, usuario=usuario))
                aplicadas += 1
            contagem_por_apto[apto] = usado + 1

        db.session.commit()
        if aplicadas:
            flash(f'{aplicadas} reserva(s) marcadas como isentas retroativamente.', 'success')
        else:
            flash('Nenhuma reserva pendente pra aplicar — todas já têm registro ou já passaram do limite.', 'info')
    except Exception as e:
        db.session.rollback()
        logging.error(f'Erro ao aplicar regra retroativamente {id}: {e}')
        flash('Erro ao aplicar a regra retroativamente.', 'danger')

    return redirect(url_for('relatorios.editar_regra_precificacao', id=id))


@relatorios_bp.route('/admin/regras-precificacao/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def excluir_regra_precificacao(id):
    regra = RegraPrecificacao.query.get_or_404(id)
    try:
        db.session.delete(regra)
        db.session.commit()
        flash('Regra removida.', 'success')
    except Exception as e:
        db.session.rollback()
        logging.error(f'Erro ao excluir regra_precificacao {id}: {e}')
        flash('Erro ao remover regra.', 'danger')
    return redirect(url_for('relatorios.listar_regras_precificacao'))