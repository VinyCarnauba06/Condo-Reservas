# app/routes/fiscal_fixo.py
#
# Portal do fiscal fixo — acesso restrito ao condomínio vinculado ao usuário
# via Condominio.fiscal_id. Fluxo: reserva, comunicado e gestão do inventário.
#
# Guard de kiosk: __init__.py/_FISCAL_FIXO_ALLOWED bloqueia qualquer endpoint
# fora desta lista para perfil 'fiscal_fixo'.

import os
import logging
from datetime import date

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, abort,
)
from app import db
from app.models import (
    Condominio, Salao, Reserva,
    ItemInventario,
    RegraRegimento,
    log_audit, sanitize_text,
)
from app.routes.auth import login_required

fiscal_fixo_bp = Blueprint('fiscal_fixo', __name__, url_prefix='/portal')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fiscal_fixo_required(f):
    """Decorator: garante login + perfil fiscal_fixo."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('auth.login'))
        if session.get('usuario_perfil') != 'fiscal_fixo':
            abort(403)
        return f(*args, **kwargs)

    return decorated


def _get_condominio_do_fiscal():
    """Retorna o Condominio vinculado ao fiscal logado ou aborta 403."""
    usuario_id = session['usuario_id']
    cond = Condominio.query.filter_by(fiscal_id=usuario_id).first()
    if not cond:
        abort(403)
    return cond


def _garantir_posse_fiscal(condominio_id, reserva=None):
    """Bloqueia acesso cross-tenant de fiscal/fiscal_fixo a recurso de outro
    condomínio (IDOR). Não afeta operador/admin.

    fiscal_fixo é preso a exatamente 1 condomínio (Condominio.fiscal_id ==
    usuario_id) — checagem estrita.

    fiscal (plantão) NÃO tem condomínio único por design: cobre rota fixa
    por condomínio nos dias de semana (Condominio.fiscal_id) e pool de
    plantão nos fins de semana (Reserva.fiscal_responsavel_id,
    reivindicação livre). Pra esse perfil, reusa a mesma regra de posse já
    validada em vistorias.preencher_vistoria — precisa do objeto `reserva`
    pra isso; sem ele, não há como checar posse por reserva e a função não
    bloqueia (a rota nem é alcançável por 'fiscal' fora do kiosk).
    """
    perfil = session.get('usuario_perfil')
    if perfil not in ('fiscal', 'fiscal_fixo'):
        return

    usuario_id = session.get('usuario_id')

    if perfil == 'fiscal_fixo':
        cond = Condominio.query.filter_by(fiscal_id=usuario_id).first()
        if not cond or cond.id != condominio_id:
            abort(403)
        return

    # perfil == 'fiscal'
    if reserva is None:
        return
    if reserva.fiscal_responsavel_id and reserva.fiscal_responsavel_id != usuario_id:
        abort(403)
    if not reserva.fiscal_responsavel_id:
        condominio = reserva.salao.condominio
        if condominio.fiscal_id and condominio.fiscal_id != usuario_id:
            dia_acao = reserva.data_vistoria or reserva.data_festa
            eh_fim_de_semana = dia_acao.weekday() >= 5 if dia_acao else False
            if not eh_fim_de_semana:
                abort(403)


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@fiscal_fixo_bp.route('/')
@_fiscal_fixo_required
def portal():
    """Dashboard: vistorias do dia + próximas reservas do condomínio."""
    from datetime import timedelta
    cond  = _get_condominio_do_fiscal()
    hoje  = date.today()
    amanha = hoje + timedelta(days=1)

    reservas = (
        Reserva.query
        .join(Salao)
        .filter(
            Salao.condominio_id == cond.id,
            Reserva.data_festa >= hoje,
            Reserva.status != 'cancelado',
        )
        .order_by(Reserva.data_festa.asc())
        .limit(50)
        .all()
    )

    # Vistorias do dia (pré-festa) — apenas deste condomínio
    def _vistorias(dia):
        return (
            Reserva.query.join(Salao)
            .filter(
                Salao.condominio_id == cond.id,
                Reserva.data_vistoria == dia,
                Reserva.status != 'cancelado',
                Reserva.festa_condominio == False,
            )
            .order_by(Reserva.hora_vistoria.asc().nullslast())
            .all()
        )

    # Revistorias do dia (pós-festa = data_festa + 1, ou override)
    def _revistorias(dia):
        dia_festa = dia - timedelta(days=1)
        return (
            Reserva.query.join(Salao)
            .filter(
                Salao.condominio_id == cond.id,
                Reserva.status != 'cancelado',
                Reserva.festa_condominio == False,
                db.or_(
                    db.and_(Reserva.data_revistoria_override.is_(None), Reserva.data_festa == dia_festa),
                    Reserva.data_revistoria_override == dia,
                ),
            )
            .all()
        )

    return render_template(
        'fiscal_fixo/portal.html',
        condominio=cond,
        reservas=reservas,
        hoje=hoje,
        amanha=amanha,
        vistorias_hoje=_vistorias(hoje),
        revistorias_hoje=_revistorias(hoje),
        vistorias_amanha=_vistorias(amanha),
        revistorias_amanha=_revistorias(amanha),
    )


@fiscal_fixo_bp.route('/reserva/nova', methods=['GET', 'POST'])
@_fiscal_fixo_required
def nova_reserva():
    """Seleção de salão + data → redireciona para reservas.nova_reserva (lógica completa de operador)."""
    import json
    cond   = _get_condominio_do_fiscal()
    saloes = cond.saloes

    hoje = date.today()

    def _ocupados_json():
        ocupados = {}
        for s in saloes:
            datas = (
                db.session.query(Reserva.data_festa)
                .filter(Reserva.salao_id == s.id, Reserva.status != 'cancelado')
                .all()
            )
            ocupados[str(s.id)] = [r.data_festa.isoformat() for r in datas]
        return ocupados

    if request.method == 'POST':
        salao_id_raw = request.form.get('salao_id', '')
        data_str     = request.form.get('data_festa', '')
        try:
            salao_id = int(salao_id_raw)
        except (ValueError, TypeError):
            flash('Selecione um salão.', 'danger')
            return render_template('fiscal_fixo/nova_reserva.html',
                condominio=cond, saloes=saloes, hoje=hoje, ocupados_json=_ocupados_json())

        salao = Salao.query.get_or_404(salao_id)
        if salao.condominio_id != cond.id:
            abort(403)

        try:
            date.fromisoformat(data_str)
        except (ValueError, TypeError):
            flash('Data inválida.', 'danger')
            return render_template('fiscal_fixo/nova_reserva.html',
                condominio=cond, saloes=saloes, hoje=hoje, ocupados_json=_ocupados_json())

        return redirect(url_for('reservas.nova_reserva', salao_id=salao_id, data_festa=data_str))

    return render_template('fiscal_fixo/nova_reserva.html',
        condominio=cond, saloes=saloes, hoje=hoje, ocupados_json=_ocupados_json())


@fiscal_fixo_bp.route('/reserva/<int:reserva_id>/editar', methods=['GET', 'POST'])
@_fiscal_fixo_required
def editar_reserva(reserva_id):
    """Guard de condomínio + delega para reservas.editar_reserva."""
    cond    = _get_condominio_do_fiscal()
    reserva = Reserva.query.get_or_404(reserva_id)
    if reserva.salao.condominio_id != cond.id:
        abort(403)
    # Passa direto para a rota do operador — ela usa login_required (já logado)
    return redirect(url_for('reservas.editar_reserva', id=reserva_id))


@fiscal_fixo_bp.route('/reserva/<int:reserva_id>/cancelar', methods=['POST'])
@_fiscal_fixo_required
def cancelar_reserva(reserva_id):
    """Guard de condomínio + delega para reservas.cancelar_reserva."""
    cond    = _get_condominio_do_fiscal()
    reserva = Reserva.query.get_or_404(reserva_id)
    if reserva.salao.condominio_id != cond.id:
        abort(403)
    return redirect(url_for('reservas.cancelar_reserva', id=reserva_id))


@fiscal_fixo_bp.route('/comunicado/<int:reserva_id>')
@_fiscal_fixo_required
def gerar_comunicado(reserva_id):
    """Redireciona pra geração do comunicado existente — valida que a reserva
    é do condomínio do fiscal antes de permitir o acesso."""
    cond    = _get_condominio_do_fiscal()
    reserva = Reserva.query.get_or_404(reserva_id)
    if reserva.salao.condominio_id != cond.id:
        abort(403)
    # Reutiliza a rota de comunicado existente em relatorios.py
    return redirect(url_for('relatorios.comunicado_individual', reserva_id=reserva_id))


@fiscal_fixo_bp.route('/configuracoes', methods=['GET', 'POST'])
@_fiscal_fixo_required
def configuracoes():
    """Edita nome/valor dos salões e regras de uso do condomínio."""
    from decimal import Decimal, InvalidOperation
    cond = _get_condominio_do_fiscal()

    if request.method == 'POST':
        acao = request.form.get('acao')

        # --- SALVAR SALÃO ---
        if acao == 'salvar_salao':
            try:
                salao_id = int(request.form.get('salao_id'))
            except (ValueError, TypeError):
                abort(400)
            salao = Salao.query.get_or_404(salao_id)
            if salao.condominio_id != cond.id:
                abort(403)
            nome_raw = sanitize_text(request.form.get('nome', '').strip(), max_length=200)
            if not nome_raw:
                flash('Nome do salão é obrigatório.', 'danger')
                return redirect(url_for('fiscal_fixo.configuracoes'))
            try:
                valor = Decimal(request.form.get('valor', '').replace(',', '.'))
                if valor < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                flash('Valor inválido.', 'danger')
                return redirect(url_for('fiscal_fixo.configuracoes'))
            salao.nome  = nome_raw
            salao.valor = valor
            db.session.commit()
            log_audit(session['usuario_id'], 'salao_editado_fiscal_fixo', 'saloes', salao.id,
                      dados_depois={'nome': nome_raw, 'valor': str(valor)})
            flash(f'Salão "{nome_raw}" atualizado.', 'success')
            return redirect(url_for('fiscal_fixo.configuracoes'))

        # --- SALVAR OBSERVAÇÕES DO CONDOMÍNIO ---
        if acao == 'salvar_obs':
            obs = sanitize_text(request.form.get('observacoes', '').strip(), max_length=2000)
            cond.observacoes = obs
            db.session.commit()
            flash('Regras de uso atualizadas.', 'success')
            return redirect(url_for('fiscal_fixo.configuracoes'))

        # --- ADICIONAR REGRA ---
        if acao == 'adicionar_regra':
            texto = sanitize_text(request.form.get('texto', '').strip(), max_length=500)
            if not texto:
                flash('Texto da regra é obrigatório.', 'danger')
                return redirect(url_for('fiscal_fixo.configuracoes'))
            regra = RegraRegimento(condominio_id=cond.id, texto=texto)
            db.session.add(regra)
            db.session.commit()
            flash('Regra adicionada.', 'success')
            return redirect(url_for('fiscal_fixo.configuracoes'))

        # --- REMOVER REGRA ---
        if acao == 'remover_regra':
            try:
                regra_id = int(request.form.get('regra_id'))
            except (ValueError, TypeError):
                abort(400)
            regra = RegraRegimento.query.get_or_404(regra_id)
            if regra.condominio_id != cond.id:
                abort(403)
            db.session.delete(regra)
            db.session.commit()
            flash('Regra removida.', 'success')
            return redirect(url_for('fiscal_fixo.configuracoes'))

        abort(400)

    regras = RegraRegimento.query.filter_by(condominio_id=cond.id).all()
    return render_template('fiscal_fixo/configuracoes.html',
                           condominio=cond, regras=regras)


@fiscal_fixo_bp.route('/inventario/<int:salao_id>', methods=['GET', 'POST'])
@_fiscal_fixo_required
def inventario(salao_id):
    """Edição de itens do inventário (adicionar/remover) + upload de foto por item.
    Só disponível quando condominio.termo_fotografico == True."""
    cond  = _get_condominio_do_fiscal()
    salao = Salao.query.get_or_404(salao_id)
    if salao.condominio_id != cond.id:
        abort(403)
    if not cond.termo_fotografico:
        abort(403)  # condomínio sem termo fotográfico não tem acesso a esta tela

    itens = ItemInventario.query.filter_by(salao_id=salao_id, ativo=True).order_by(ItemInventario.ordem).all()

    if request.method == 'POST':
        acao = request.form.get('acao')

        # --- ADICIONAR ITEM ---
        if acao == 'adicionar_item':
            descricao = sanitize_text(request.form.get('descricao', '').strip(), max_length=200)
            if not descricao:
                flash('Descrição do item é obrigatória.', 'danger')
                return redirect(url_for('fiscal_fixo.inventario', salao_id=salao_id))
            # Reativa item soft-deleted com mesma descrição se existir
            existente = ItemInventario.query.filter_by(
                salao_id=salao_id, descricao=descricao
            ).first()
            if existente:
                existente.ativo = True
            else:
                ordem_max = db.session.query(
                    db.func.max(ItemInventario.ordem)
                ).filter_by(salao_id=salao_id).scalar() or 0
                novo = ItemInventario(
                    salao_id=salao_id, descricao=descricao, ordem=ordem_max + 1
                )
                db.session.add(novo)
            db.session.commit()
            flash(f'Item "{descricao}" adicionado.', 'success')
            return redirect(url_for('fiscal_fixo.inventario', salao_id=salao_id))

        # --- REMOVER ITEM (soft-delete) ---
        if acao == 'remover_item':
            try:
                item_id = int(request.form.get('item_id'))
            except (ValueError, TypeError):
                abort(400)
            item = ItemInventario.query.get_or_404(item_id)
            if item.salao_id != salao_id:
                abort(403)
            item.ativo = False
            db.session.commit()
            flash('Item removido.', 'success')
            return redirect(url_for('fiscal_fixo.inventario', salao_id=salao_id))

        abort(400)

    return render_template(
        'fiscal_fixo/inventario.html',
        condominio=cond,
        salao=salao,
        itens=itens,
    )
