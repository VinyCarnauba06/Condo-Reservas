import traceback
from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import Salao, Condominio, Usuario, sanitize_text
from app.routes.auth import login_required, admin_required
import re
from decimal import Decimal, InvalidOperation

saloes_bp = Blueprint('saloes', __name__)

_NOME_RE     = re.compile(r'^[a-zA-Z0-9\s\+\-àáäâãèéëêìíïîòóöôõùúüûæœçñÀÁÄÂÃÈÉËÊÌÍÏÎÒÓÖÔÕÙÚÜÛÆŒÇÑ]+$')
_HORARIO_RE  = re.compile(r'^([0-1][0-9]|2[0-3]):[0-5][0-9]-([0-1][0-9]|2[0-3]):[0-5][0-9]$')
_VALOR_MIN   = Decimal('0.00')
_VALOR_MAX   = Decimal('99999.99')


def _validar_salao_fields(form):
    erros = []
    nome = sanitize_text(form.get('nome', ''), 200) or ''
    if len(nome) < 3:
        erros.append('Nome do salão deve ter pelo menos 3 caracteres.')
    elif not _NOME_RE.match(nome):
        erros.append('Nome do salão inválido (use letras, números, espaços, hífens e +).')
    try:
        valor = Decimal(str(form.get('valor', '') or '0'))
        if not (_VALOR_MIN <= valor <= _VALOR_MAX):
            erros.append(f'Valor inválido. Use entre R$ {_VALOR_MIN} e R$ {_VALOR_MAX}.')
    except (InvalidOperation, ValueError, TypeError):
        erros.append('Valor inválido. Informe um número válido.')
    horarios_raw = [h.strip() for h in form.getlist('horario_faixa') if h.strip()]
    for h in horarios_raw:
        if not _HORARIO_RE.match(h):
            erros.append(f'Horário "{h}" inválido. Use o formato HH:MM-HH:MM (ex: 14:00-18:00).')
            break
    return erros


@saloes_bp.route('/condominio/<int:condominio_id>/salao/novo', methods=['GET', 'POST'])
@login_required
def novo_salao(condominio_id):
    condominio = Condominio.query.get_or_404(condominio_id)
    if request.method == 'POST':
        erros = _validar_salao_fields(request.form)
        if erros:
            for erro in erros:
                flash(erro, 'danger')
            return render_template('form_salao.html', condominio=condominio, salao=None)

        try:
            valor = float(Decimal(str(request.form.get('valor') or '0')))
            horarios_lista = [sanitize_text(h, 50) for h in request.form.getlist('horario_faixa') if h.strip()]
            horarios_lista = [h for h in horarios_lista if h]
            salao = Salao(
                condominio_id   = condominio_id,
                nome            = sanitize_text(request.form.get('nome', ''), 200) or request.form.get('nome', '')[:200],
                valor           = valor,
                forma_pagamento = request.form.get('forma_pagamento', 'Boleto Antecipado'),
                tipo            = request.form.get('tipo', 'dia_inteiro'),
                horarios        = ','.join(horarios_lista),
                zelador         = request.form.get('zelador', 'nunca'),
                grupo           = request.form.get('grupo') or None,
                combo           = request.form.get('combo') == '1'
            )
            db.session.add(salao)
            from sqlalchemy import text
            if db.engine.dialect.name == 'postgresql':
                db.session.execute(text(
                    "SELECT setval('saloes_id_seq', coalesce((SELECT MAX(id) FROM saloes), 1))"
                ))
            db.session.commit()
        except Exception:
            traceback.print_exc()
            db.session.rollback()
            flash('Erro ao salvar salão. Tente novamente.', 'danger')
            return render_template('form_salao.html', condominio=condominio, salao=None)
        return redirect(url_for('condominios.ver_condominio', id=condominio_id))
    return render_template('form_salao.html', condominio=condominio, salao=None)


@saloes_bp.route('/salao/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_salao(id):
    salao = Salao.query.get_or_404(id)
    if request.method == 'POST':
        erros = _validar_salao_fields(request.form)
        if erros:
            for erro in erros:
                flash(erro, 'danger')
            return render_template('form_salao.html', condominio=salao.condominio, salao=salao)

        try:
            valor = float(Decimal(str(request.form.get('valor') or '0')))
            horarios_lista = [sanitize_text(h, 50) for h in request.form.getlist('horario_faixa') if h.strip()]
            horarios_lista = [h for h in horarios_lista if h]
            salao.nome            = sanitize_text(request.form.get('nome', ''), 200) or request.form.get('nome', '')[:200]
            salao.valor           = valor
            salao.forma_pagamento = request.form.get('forma_pagamento', 'Boleto Antecipado')
            salao.tipo            = request.form.get('tipo', 'dia_inteiro')
            salao.horarios        = ','.join(horarios_lista)
            salao.zelador         = request.form.get('zelador', 'nunca')
            salao.grupo           = request.form.get('grupo') or None
            salao.combo           = request.form.get('combo') == '1'
            db.session.commit()
        except Exception:
            traceback.print_exc()
            db.session.rollback()
            flash('Erro ao salvar alterações. Tente novamente.', 'danger')
            return render_template('form_salao.html', condominio=salao.condominio, salao=salao)
        return redirect(url_for('condominios.ver_condominio', id=salao.condominio_id))
    return render_template('form_salao.html', condominio=salao.condominio, salao=salao)


@saloes_bp.route('/salao/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def excluir_salao(id):
    senha = request.form.get('senha_admin', '')
    from flask import session
    usuario_atual = Usuario.query.get(session.get('usuario_id'))
    if not usuario_atual or not usuario_atual.checar_senha(senha):
        flash('Senha incorreta.', 'error')
        condominio_id = request.form.get('condominio_id', '')
        return redirect(url_for('condominios.ver_condominio', id=condominio_id) if condominio_id else url_for('condominios.home'))
    salao = Salao.query.get_or_404(id)
    condominio_id = salao.condominio_id
    try:
        db.session.delete(salao)
        db.session.commit()
    except Exception:
        traceback.print_exc()
        db.session.rollback()
        flash('Erro ao excluir salão. Verifique se há reservas associadas.', 'danger')
        return redirect(url_for('condominios.ver_condominio', id=condominio_id))
    return redirect(url_for('condominios.ver_condominio', id=condominio_id))
