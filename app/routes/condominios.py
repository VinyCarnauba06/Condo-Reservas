from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app import db
from app.models import Condominio, Salao, Reserva, Credito, Feriado, Bloqueio, Usuario, sanitize_text
from app.routes.auth import login_required, admin_required
from datetime import date, timedelta, datetime
import calendar
import pytz

def get_hoje_br():
    return datetime.now(pytz.timezone('America/Sao_Paulo')).date()
import logging
import re

_NOME_RE = re.compile(r'^[a-zA-Z0-9\s\.\,\'\"\(\)\/\\\+\-àáäâãèéëêìíïîòóöôõùúüûæœçñÀÁÄÂÃÈÉËÊÌÍÏÎÒÓÖÔÕÙÚÜÛÆŒÇÑ]+$')

condominios_bp = Blueprint('condominios', __name__)


def _mes_ano_validos(mes, ano):
    return 1 <= mes <= 12 and 2000 <= ano <= 2100


def _fiscais_ativos():
    # [TESTE] vistoria mobile — lista pro dropdown de fiscal designado
    return Usuario.query.filter_by(perfil='fiscal', ativo=True).order_by(Usuario.nome).all()


def _fiscais_fixos_ativos():
    # [FEATURE] fiscal fixo — dropdown no form do condomínio
    return Usuario.query.filter_by(perfil='fiscal_fixo', ativo=True).order_by(Usuario.nome).all()


@condominios_bp.route('/')
@login_required
def home():
    hoje         = get_hoje_br()
    amanha       = hoje + timedelta(days=1)
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia   = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    condominios  = (
        Condominio.query
        .options(selectinload(Condominio.saloes))
        .order_by(Condominio.nome)
        .all()
    )

    total_condominios = len(condominios)
    total_saloes      = sum(len(c.saloes) for c in condominios)

    reservas_mes = (
        Reserva.query.join(Salao).join(Condominio)
        .filter(Reserva.data_festa >= primeiro_dia, Reserva.data_festa <= ultimo_dia)
        .order_by(Reserva.data_festa)
        .all()
    )

    lista_mes        = [r for r in reservas_mes if r.status != 'cancelado']
    lista_pendentes  = [r for r in reservas_mes if r.status == 'pendente']
    lista_cancelados = [r for r in reservas_mes if r.status == 'cancelado']
    # [FIX] data_vistoria fica gravado na reserva com base no emite_termo do
    # condomínio no momento da criação — se o admin desativar depois, reserva
    # antiga ficava listada aqui mesmo sem termo a ser feito. Filtra pelo
    # emite_termo atual pra não exibir vistoria que não existe mais.
    #
    # [FIX] mesmo problema pra fiscal_proprio_seg_sab: reserva criada antes
    # do condomínio marcar "fiscal próprio" continua com data_vistoria
    # preenchido num dia de semana. Se o dia não é domingo, exclui condomínio
    # com fiscal próprio (reconsulta a regra atual, não confia no campo antigo).
    _query_vistorias_hoje = Reserva.query.join(Salao).join(Condominio) \
        .filter(Reserva.data_vistoria == hoje,
                Reserva.status != 'cancelado',
                Condominio.emite_termo == True)
    if hoje.weekday() != 6:
        _query_vistorias_hoje = _query_vistorias_hoje.filter(Condominio.fiscal_proprio_seg_sab == False)
    lista_vistorias_hoje = _query_vistorias_hoje.all()

    _query_vistorias_amanha = Reserva.query.join(Salao).join(Condominio) \
        .filter(Reserva.data_vistoria == amanha,
                Reserva.status != 'cancelado',
                Condominio.emite_termo == True)
    if amanha.weekday() != 6:
        _query_vistorias_amanha = _query_vistorias_amanha.filter(Condominio.fiscal_proprio_seg_sab == False)
    lista_vistorias = _query_vistorias_amanha.all()

    total_mes        = len(lista_mes)
    total_pendentes  = len(lista_pendentes)
    total_cancelados = len(lista_cancelados)
    vistorias_hoje   = len(lista_vistorias_hoje)
    vistorias_amanha = len(lista_vistorias)

    ranking = (
        db.session.query(Condominio, func.count(Reserva.id).label('total'))
        .join(Salao, Salao.condominio_id == Condominio.id)
        .join(Reserva, Reserva.salao_id == Salao.id)
        .filter(Reserva.data_festa >= primeiro_dia, Reserva.data_festa <= ultimo_dia)
        .filter(Reserva.status != 'cancelado')
        .group_by(Condominio.id)
        .order_by(func.count(Reserva.id).desc())
        .limit(8)
        .all()
    )

    return render_template('home.html',
        total_condominios=total_condominios,
        total_saloes=total_saloes,
        total_mes=total_mes,
        total_pendentes=total_pendentes,
        total_cancelados=total_cancelados,
        vistorias_hoje=vistorias_hoje,
        vistorias_amanha=vistorias_amanha,
        lista_pendentes=lista_pendentes,
        lista_cancelados=lista_cancelados,
        lista_vistorias_hoje=lista_vistorias_hoje,
        lista_vistorias=lista_vistorias,
        lista_mes=lista_mes,
        ranking=ranking,
    )


@condominios_bp.route('/condominios')
@login_required
def lista_condominios():
    busca = request.args.get('busca', '')
    page  = request.args.get('page', 1, type=int)
    q = Condominio.query.options(selectinload(Condominio.saloes)).order_by(Condominio.nome)
    if busca:
        q = q.filter(Condominio.nome.ilike(f'%{busca}%'))
    pagination = q.paginate(page=page, per_page=20, error_out=False)
    return render_template('condominios_lista.html', condominios=pagination.items, pagination=pagination, busca=busca)


@condominios_bp.route('/condominio/<int:id>')
@login_required
def ver_condominio(id):
    condominio   = Condominio.query.get_or_404(id)
    hoje         = get_hoje_br()
    mes          = request.args.get('mes', hoje.month, type=int)
    ano          = request.args.get('ano', hoje.year,  type=int)
    busca        = request.args.get('busca', '')

    if not _mes_ano_validos(mes, ano):
        mes, ano = hoje.month, hoje.year

    primeiro_dia = date(ano, mes, 1)
    ultimo_dia   = date(ano, mes, calendar.monthrange(ano, mes)[1])

    reservas_mes = (
        Reserva.query.join(Salao)
        .filter(Salao.condominio_id == id)
        .filter(Reserva.data_festa >= primeiro_dia)
        .filter(Reserva.data_festa <= ultimo_dia)
        .all()
    )

    reservas_map = {}
    for r in reservas_mes:
        reservas_map.setdefault(r.data_festa, []).append(r)

    dias_vistoria = set()
    for r in reservas_mes:
        # [FIX] mesma reconsulta live de precisa_vistoria_em() — reserva
        # antiga pode ter data_vistoria congelado de antes do condomínio
        # marcar fiscal_proprio_seg_sab (não é mais vistoria da Administradora hoje).
        if r.status != 'cancelado' and condominio.precisa_vistoria_em(r.data_vistoria):
            dias_vistoria.add(r.data_vistoria)

    feriados_mes  = Feriado.query.filter(
        Feriado.mes == mes,
        db.or_(Feriado.ano == None, Feriado.ano == ano)
    ).all()
    feriados_dias = {f.dia for f in feriados_mes}

    bloqueios_mes  = Bloqueio.query.filter(
        db.or_(Bloqueio.condominio_id == None, Bloqueio.condominio_id == id),
        Bloqueio.data_inicio <= ultimo_dia,
        Bloqueio.data_fim    >= primeiro_dia
    ).all()
    bloqueios_dias = {}
    for b in bloqueios_mes:
        d = b.data_inicio
        while d <= b.data_fim:
            if d.month == mes and d.year == ano:
                bloqueios_dias[d.day] = b.descricao
            d = d + timedelta(days=1)

    creditos = Credito.query.filter_by(condominio_id=id, usado=False).all()

    reservas_busca = []
    if busca:
        reservas_todas = (
            Reserva.query.join(Salao)
            .filter(Salao.condominio_id == id)
            .order_by(Reserva.data_festa.desc())
            .all()
        )
        busca_lower = busca.lower()
        reservas_busca = [
            r for r in reservas_todas
            if busca_lower in (r.nome_solicitante or '').lower()
            or busca_lower in (r.apartamento or '').lower()
        ]

    lista_reservas_ativas = [r for r in reservas_mes if r.status != 'cancelado']
    lista_cancelados_mes  = [r for r in reservas_mes if r.status == 'cancelado']
    pendentes_mes         = [r for r in reservas_mes if r.status == 'pendente']
    total_mes             = len(lista_reservas_ativas)
    semana_cal            = calendar.monthcalendar(ano, mes)
    mes_anterior          = primeiro_dia - timedelta(days=1)
    mes_seguinte          = ultimo_dia + timedelta(days=1)

    return render_template('condominio.html',
        condominio=condominio,
        reservas_map=reservas_map,
        feriados_dias=feriados_dias,
        bloqueios_dias=bloqueios_dias,
        creditos=creditos,
        reservas_busca=reservas_busca,
        busca=busca,
        total_mes=total_mes,
        semana_cal=semana_cal,
        mes=mes,
        ano=ano,
        mes_anterior=mes_anterior,
        mes_seguinte=mes_seguinte,
        dias_vistoria=dias_vistoria,
        hoje=hoje,
        calendar=calendar,
        lista_reservas_ativas=lista_reservas_ativas,
        lista_cancelados_mes=lista_cancelados_mes,
        pendentes_mes=pendentes_mes,
    )


def _validar_nome_cond(nome):
    if len(nome) < 3:
        return 'Nome deve ter pelo menos 3 caracteres.'
    if len(nome) > 200:
        return 'Nome muito longo (máx. 200 caracteres).'
    if not _NOME_RE.match(nome):
        return 'Nome inválido (use apenas letras, números, espaços e hífens).'
    return None


@condominios_bp.route('/condominio/novo', methods=['GET', 'POST'])
@login_required
def novo_condominio():
    if request.method == 'POST':
        nome_raw = sanitize_text(request.form.get('nome', ''), 200) or ''
        erro_nome = _validar_nome_cond(nome_raw)
        if erro_nome:
            flash(erro_nome, 'danger')
            return render_template('form_condominio.html', condominio=None)
        try:
            qtd_com = int(request.form.get('qtd_comunicados', 3))
            qtd_com = max(1, min(10, qtd_com))
        except (ValueError, TypeError):
            qtd_com = 3
        condominio = Condominio(
            nome              = nome_raw,
            endereco          = sanitize_text(request.form.get('endereco', ''), 300),
            observacoes       = sanitize_text(request.form.get('observacoes', ''), 500),
            comunicados_salao = sanitize_text(request.form.get('comunicados_salao', ''), 2000),
            qtd_comunicados   = qtd_com,
            exige_adimplencia = request.form.get('exige_adimplencia') == '1',
            emite_comunicado  = 'emite_comunicado' in request.form,
            emite_termo       = 'emite_termo' in request.form,
        )
        try:
            db.session.add(condominio)
            db.session.flush()

            nomes    = request.form.getlist('salao_nome')
            valores  = request.form.getlist('salao_valor')
            formas   = request.form.getlist('salao_forma')
            tipos    = request.form.getlist('salao_tipo')
            horarios = request.form.getlist('salao_horarios')
            zeladors = request.form.getlist('salao_zelador')
            grupos   = request.form.getlist('salao_grupo')
            combos   = request.form.getlist('salao_combo')

            for i, nome in enumerate(nomes):
                if nome.strip():
                    try:
                        valor = float(valores[i]) if i < len(valores) and valores[i] else 0.0
                    except ValueError:
                        valor = 0.0
                    salao = Salao(
                        condominio_id   = condominio.id,
                        nome            = nome.strip(),
                        valor           = valor,
                        forma_pagamento = formas[i] if i < len(formas) else 'Boleto Antecipado',
                        tipo            = tipos[i] if i < len(tipos) else 'dia_inteiro',
                        horarios        = horarios[i] if i < len(horarios) else '',
                        zelador         = zeladors[i] if i < len(zeladors) else 'nunca',
                        grupo           = grupos[i] if i < len(grupos) and grupos[i] else None,
                        combo           = combos[i] == '1' if i < len(combos) else False
                    )
                    db.session.add(salao)

            db.session.commit()
        except Exception:
            db.session.rollback()
            logging.exception('Erro ao salvar novo condomínio')
            flash('Erro ao salvar condomínio. Tente novamente.', 'danger')
            return render_template('form_condominio.html', condominio=None)
        return redirect(url_for('condominios.home'))
    return render_template('form_condominio.html', condominio=None)


@condominios_bp.route('/condominio/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_condominio(id):
    condominio = Condominio.query.get_or_404(id)
    if request.method == 'POST':
        nome_raw = sanitize_text(request.form.get('nome', ''), 200) or ''
        erro_nome = _validar_nome_cond(nome_raw)
        if erro_nome:
            flash(erro_nome, 'danger')
            return render_template('form_condominio.html', condominio=condominio)
        try:
            qtd_com = int(request.form.get('qtd_comunicados', 3))
            qtd_com = max(1, min(10, qtd_com))
        except (ValueError, TypeError):
            qtd_com = 3
        condominio.nome              = nome_raw
        condominio.endereco          = sanitize_text(request.form.get('endereco', ''), 300)
        condominio.observacoes       = sanitize_text(request.form.get('observacoes', ''), 500)
        condominio.comunicados_salao = sanitize_text(request.form.get('comunicados_salao', ''), 2000)
        condominio.qtd_comunicados   = qtd_com
        condominio.exige_adimplencia = request.form.get('exige_adimplencia') == '1'
        condominio.emite_comunicado  = 'emite_comunicado' in request.form
        condominio.emite_termo       = 'emite_termo' in request.form
        # [TESTE] fiscal próprio (seg-sáb) — só domingo vira vistoria da Administradora
        condominio.fiscal_proprio_seg_sab = 'fiscal_proprio_seg_sab' in request.form
        # [TESTE] termo na portaria — troca o termo legal completo por aviso
        # simples de apoio fiscal (Residencial Costa Azul, Edifício Panorama)
        condominio.termo_na_portaria = 'termo_na_portaria' in request.form
        # [TESTE] vistoria mobile — fiscal fixo pra dias de semana
        fiscal_id_raw = request.form.get('fiscal_id', '')
        condominio.fiscal_id = int(fiscal_id_raw) if fiscal_id_raw.isdigit() else None
        # [TESTE] recibo de zelador — valor cobrado + observação livre
        valor_zelador_raw = request.form.get('valor_zelador', '').strip().replace(',', '.')
        try:
            condominio.valor_zelador = round(float(valor_zelador_raw), 2) if valor_zelador_raw else None
        except (ValueError, TypeError):
            condominio.valor_zelador = None
        condominio.obs_zelador = sanitize_text(request.form.get('obs_zelador', ''), 300)
        condominio.zelador_pago_direto_fiscal = 'zelador_pago_direto_fiscal' in request.form
        # [FEATURE] fiscal fixo — vínculo do fiscal_fixo ao condomínio
        fiscal_fixo_id_raw = request.form.get('fiscal_fixo_id', '')
        condominio.fiscal_id = (
            int(fiscal_fixo_id_raw)
            if fiscal_fixo_id_raw.isdigit() and fiscal_fixo_id_raw != '0'
            else condominio.fiscal_id  # preserva fiscal de vistoria se não veio no form
        )
        # [FEATURE] termo fotográfico — KAÁ e futuros condomínios com esse fluxo
        condominio.termo_fotografico = 'termo_fotografico' in request.form
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logging.exception('Erro ao editar condomínio id=%s', id)
            flash('Erro ao salvar alterações.', 'danger')
            return render_template(
                'form_condominio.html', condominio=condominio,
                fiscais=_fiscais_ativos(), fiscais_fixos=_fiscais_fixos_ativos()
            )
        return redirect(url_for('condominios.ver_condominio', id=id))
    return render_template(
        'form_condominio.html', condominio=condominio,
        fiscais=_fiscais_ativos(), fiscais_fixos=_fiscais_fixos_ativos()
    )


@condominios_bp.route('/condominio/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def excluir_condominio(id):
    senha = request.form.get('senha_admin', '')
    usuario_atual = Usuario.query.get(session.get('usuario_id'))
    if not usuario_atual or not usuario_atual.checar_senha(senha):
        flash('Senha incorreta.', 'error')
        return redirect(url_for('condominios.editar_condominio', id=id))
    condominio = Condominio.query.get_or_404(id)
    try:
        db.session.delete(condominio)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logging.exception('Erro ao excluir condomínio id=%s', id)
        flash('Erro ao excluir condomínio.', 'error')
        return redirect(url_for('condominios.editar_condominio', id=id))
    return redirect(url_for('condominios.home'))


# ── Copa do Mundo 2026 ────────────────────────────────────────────────────────
