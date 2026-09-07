from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash, jsonify
from app import db
from app.models import Reserva, Salao, Condominio, Historico, Credito, RegraPrecificacao, ReservaAnualUnidade, log_audit, sanitize_text
from app.routes.auth import login_required
from app.routes.fiscal_fixo import _garantir_posse_fiscal
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
import logging
import re

reservas_bp = Blueprint('reservas', __name__)

_APT_RE = re.compile(r'^[0-9]{2,3}[A-Z0-9\-]*$')


def _parse_data(data_str):
    try:
        return date.fromisoformat(data_str)
    except (ValueError, TypeError):
        abort(404)


def _zelador_val(salao, form, data_festa):
    if salao.zelador == 'sempre':
        return True
    if salao.zelador == 'opcional':
        return 'zelador' in form
    if salao.zelador == 'fds':
        return data_festa.weekday() >= 5  # 5=sábado, 6=domingo
    return False


def _checar_regra_precificacao(salao_id, condominio_id, apartamento):
    """
    Retorna (regra, isento, contagem_atual).
    isento=True se contagem < regra.limite_reservas.
    Retorna (None, False, 0) se não há regra ativa para o salão.
    """
    regra = RegraPrecificacao.query.filter_by(salao_id=salao_id, ativo=True).first()
    if not regra or not apartamento:
        return None, False, 0

    ano_atual = date.today().year
    # [FIX] contagem não filtrava por salao_id — regra é por salão, mas a
    # cota era consumida em qualquer salão do condomínio. Corrigido pra
    # escopar por salão, batendo com o que a regra sempre foi.
    contagem  = ReservaAnualUnidade.query.filter_by(
        condominio_id=condominio_id,
        salao_id=salao_id,
        apartamento=apartamento.strip().upper(),
        ano=ano_atual,
    ).count()

    isento = contagem < regra.limite_reservas
    return regra, isento, contagem


def _validar_reserva(form, data):
    erros = []
    nome = form.get('nome_solicitante', '').strip()
    if len(nome) < 3:
        erros.append('Nome do solicitante deve ter pelo menos 3 caracteres.')
    elif len(nome) > 200:
        erros.append('Nome do solicitante muito longo (máx. 200 caracteres).')
    apt = form.get('apartamento', '').strip()
    if not apt or not _APT_RE.match(apt.upper()) or len(apt) > 20:
        erros.append('Apartamento inválido (ex: 401, 704-M, 602-2, 301A).')
    if len(form.get('observacoes', '')) > 500:
        erros.append('Observações não podem ultrapassar 500 caracteres.')
    return erros


@reservas_bp.route('/reserva/nova/<int:salao_id>/<string:data_festa>', methods=['GET', 'POST'])
@login_required
def nova_reserva(salao_id, data_festa):
    salao      = Salao.query.get_or_404(salao_id)
    condominio = salao.condominio
    if condominio is None:
        flash('Salão sem condomínio associado. Contate o administrador.', 'danger')
        return redirect(url_for('condominios.home'))
    _garantir_posse_fiscal(condominio.id)

    data    = _parse_data(data_festa)
    usuario = session.get('usuario_login', session.get('usuario_nome', ''))

    horarios_disponiveis = [h.strip() for h in (salao.horarios or '').split(',') if h.strip()]

    vistoria_bloqueada = Reserva.query.filter_by(
        salao_id=salao_id, data_festa=data - timedelta(days=1)
    ).filter(Reserva.status != 'cancelado').first() is not None

    if salao.tipo == 'dia_inteiro':
        conflito = Reserva.query.filter_by(
            salao_id=salao_id, data_festa=data
        ).filter(Reserva.status != 'cancelado').first()
        if conflito:
            flash('Essa data já está reservada para este salão.', 'danger')
            return redirect(url_for('condominios.ver_condominio', id=condominio.id,
                mes=data.month, ano=data.year))

        if salao.grupo:
            saloes_grupo = Salao.query.filter_by(
                condominio_id=condominio.id, grupo=salao.grupo
            ).filter(Salao.id != salao_id).all()
            for s in saloes_grupo:
                conflito_grupo = Reserva.query.filter_by(
                    salao_id=s.id, data_festa=data
                ).filter(Reserva.status != 'cancelado').first()
                if conflito_grupo:
                    if salao.combo:
                        flash('Essa data já está reservada para um salão do mesmo grupo.', 'danger')
                        return redirect(url_for('condominios.ver_condominio', id=condominio.id,
                            mes=data.month, ano=data.year))
                    else:
                        if s.combo:
                            flash('Essa data já está reservada para um salão do mesmo grupo.', 'danger')
                            return redirect(url_for('condominios.ver_condominio', id=condominio.id,
                                mes=data.month, ano=data.year))

    credito = Credito.query.filter_by(
        condominio_id=condominio.id, salao_id=salao_id, usado=False
    ).order_by(Credito.criado_em.asc()).first()

    _regra_preview       = RegraPrecificacao.query.filter_by(salao_id=salao_id, ativo=True).first()
    regra_preview        = _regra_preview
    regra_isenta_preview = False

    if request.method == 'POST':
        festa_cond = 'festa_condominio' in request.form

        if not festa_cond:
            erros = _validar_reserva(request.form, data)
            if erros:
                for erro in erros:
                    flash(erro, 'danger')
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=data,
                    credito=credito, horarios_disponiveis=horarios_disponiveis,
                    vistoria_bloqueada=vistoria_bloqueada)

        horario_escolhido = request.form.get('horario', '').strip()
        if horario_escolhido and horarios_disponiveis and horario_escolhido not in horarios_disponiveis:
            flash('Formato de horário inválido.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=data,
                credito=credito, horarios_disponiveis=horarios_disponiveis,
                vistoria_bloqueada=vistoria_bloqueada)

        if salao.tipo == 'horario_fixo' and horario_escolhido:
            # [FIX] Reserva.salao/Salao.condominio são lazy='joined' — todo
            # Reserva.query já vem com LEFT OUTER JOIN saloes/condominios
            # embutido. Postgres recusa FOR UPDATE no lado nullable de outer
            # join ("FeatureNotSupported"). of=Reserva restringe o lock só à
            # tabela reservas (FOR UPDATE OF reservas), que é permitido.
            conflito_horario = Reserva.query.filter_by(
                salao_id=salao_id, data_festa=data, horario=horario_escolhido
            ).filter(Reserva.status != 'cancelado').with_for_update(of=Reserva).first()
            if conflito_horario:
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=data,
                    credito=credito, horarios_disponiveis=horarios_disponiveis,
                    erro_conflito=horario_escolhido,
                    vistoria_bloqueada=vistoria_bloqueada)

        # [FIX] a checagem de conflito lá em cima (linha ~90) roda também no
        # GET e sem lock — só serve pra desviar o acesso ao formulário quando
        # a data já tá óbvia e ocupada. Ela sozinha não protege contra duas
        # pessoas do setor submetendo o POST quase ao mesmo tempo pra mesma
        # data (dia_inteiro/grupo), que é o cenário real em pico de alta
        # temporada. Re-checa aqui, dentro da mesma transação do INSERT,
        # com FOR UPDATE, igual já era feito pro horario_fixo.
        if salao.tipo == 'dia_inteiro':
            conflito_final = Reserva.query.filter_by(
                salao_id=salao_id, data_festa=data
            ).filter(Reserva.status != 'cancelado').with_for_update(of=Reserva).first()
            if conflito_final:
                flash('Essa data acabou de ser reservada por outra pessoa. Atualize a página.', 'danger')
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=data,
                    credito=credito, horarios_disponiveis=horarios_disponiveis,
                    vistoria_bloqueada=vistoria_bloqueada)

            if salao.grupo:
                saloes_grupo_ids = [
                    s.id for s in Salao.query.filter_by(
                        condominio_id=condominio.id, grupo=salao.grupo
                    ).filter(Salao.id != salao_id).all()
                ]
                if saloes_grupo_ids:
                    conflito_grupo_final = Reserva.query.filter(
                        Reserva.salao_id.in_(saloes_grupo_ids),
                        Reserva.data_festa == data,
                        Reserva.status != 'cancelado'
                    ).with_for_update(of=Reserva).first()
                    if conflito_grupo_final:
                        s_conflito = Salao.query.get(conflito_grupo_final.salao_id)
                        bloqueia = salao.combo or (s_conflito and s_conflito.combo)
                        if bloqueia:
                            flash('Essa data acabou de ser reservada por outra pessoa (salão do mesmo grupo). Atualize a página.', 'danger')
                            return render_template('formulario.html',
                                salao=salao, condominio=condominio, data_festa=data,
                                credito=credito, horarios_disponiveis=horarios_disponiveis,
                                vistoria_bloqueada=vistoria_bloqueada)

        try:
            # [FIX] tem_vistoria não checava emite_termo (formula divergia da
            # documentada em .claude/business-rules.md). Agora também respeita
            # fiscal_proprio_seg_sab: condomínio com fiscal fixo próprio (seg-
            # sáb) só precisa de vistoria da administradora se o dia da
            # vistoria (data_festa, ou D-1 se antecipada) cair num domingo —
            # ver Condominio.precisa_vistoria_em() em app/models.py.
            antecipa_desejada       = (not festa_cond) and ('vistoria' in request.form)
            data_vistoria_candidata = (data - timedelta(days=1)) if antecipa_desejada else data
            tem_vistoria  = (not festa_cond) and condominio.precisa_vistoria_em(data_vistoria_candidata)
            data_vistoria = data_vistoria_candidata if tem_vistoria else None
            hora_vistoria_raw = request.form.get('hora_vistoria', '').strip()
            hora_vistoria     = hora_vistoria_raw if (
                tem_vistoria and
                re.match(r'^([01]\d|2[0-3]):[0-5]\d$', hora_vistoria_raw)
            ) else None

            if festa_cond:
                nome_sol = None
                contato_val = None
                apartamento_val = None
            else:
                nome_sol = request.form['nome_solicitante'].strip()
                contato_val = request.form.get('contato', '').strip()
                apartamento_val = request.form['apartamento'].strip()

            # --- Regra de precificação especial ---
            regra_prec, isento, contagem_atual = None, False, 0
            usar_opcional = False
            if not festa_cond and apartamento_val:
                regra_prec, isento, contagem_atual = _checar_regra_precificacao(
                    salao_id, condominio.id, apartamento_val
                )
                usar_opcional = (
                    isento and
                    regra_prec is not None and
                    regra_prec.valor_opcional is not None and
                    'usar_opcional' in request.form
                )

            reserva = Reserva(
                salao_id         = salao_id,
                nome_solicitante = nome_sol,
                contato          = contato_val,
                apartamento      = apartamento_val,
                data_festa       = data,
                data_vistoria    = data_vistoria,
                hora_vistoria    = hora_vistoria,
                horario          = horario_escolhido,
                status           = request.form.get('status', 'confirmado') if request.form.get('status') in {'confirmado', 'pendente'} else 'confirmado',
                vistoria         = tem_vistoria,
                zelador          = _zelador_val(salao, request.form, data),
                surpresa         = 'surpresa' in request.form,
                festa_condominio = festa_cond,
                bloqueia_dia_todo = (salao.tipo == 'dia_inteiro'),
                registrado_por   = usuario,
                observacoes      = sanitize_text(request.form.get('observacoes', ''), 500)
            )
            db.session.add(reserva)
            db.session.flush()

            # --- Vistoria combinada: festa em dias seguidos, mesma unidade,
            # mesmo salão. Quem entra depois (2º dia) não tem vistoria
            # própria; a revistoria da 1ª reserva passa a ser D+1 depois do
            # ÚLTIMO dia, não do primeiro. Busca por salao_id+data_festa
            # (não indexados por criptografia) e só então compara o
            # apartamento decriptado em memória — nunca filtrar
            # apartamento/nome/contato direto no SQLAlchemy (são ciphertext
            # no banco, ver .claude/database-rules.md).
            if not festa_cond and apartamento_val:
                apt_norm = apartamento_val.strip().upper()

                anterior = Reserva.query.filter_by(
                    salao_id=salao_id, data_festa=data - timedelta(days=1)
                ).filter(Reserva.status != 'cancelado', Reserva.festa_condominio == False).first()
                if anterior and (anterior.apartamento or '').strip().upper() == apt_norm:
                    reserva.vistoria                = False
                    reserva.data_vistoria            = None
                    reserva.hora_vistoria            = None
                    reserva.vistoria_pareada_com_id  = anterior.id
                    anterior.data_revistoria_override = data + timedelta(days=1)
                    db.session.add(Historico(
                        reserva_id = reserva.id,
                        descricao  = f'Vistoria combinada com a reserva de {anterior.data_festa.strftime("%d/%m/%Y")} (mesma unidade, dias seguidos) — sem vistoria própria',
                        usuario    = usuario
                    ))
                    db.session.add(Historico(
                        reserva_id = anterior.id,
                        descricao  = f'Revistoria remarcada para {(data + timedelta(days=1)).strftime("%d/%m/%Y")} — festa combinada com {data.strftime("%d/%m/%Y")}',
                        usuario    = usuario
                    ))

                seguinte = Reserva.query.filter_by(
                    salao_id=salao_id, data_festa=data + timedelta(days=1)
                ).filter(Reserva.status != 'cancelado', Reserva.festa_condominio == False).first()
                if seguinte and (seguinte.apartamento or '').strip().upper() == apt_norm:
                    seguinte.vistoria                = False
                    seguinte.data_vistoria            = None
                    seguinte.hora_vistoria            = None
                    seguinte.vistoria_pareada_com_id  = reserva.id
                    reserva.data_revistoria_override  = seguinte.data_festa + timedelta(days=1)
                    db.session.add(Historico(
                        reserva_id = seguinte.id,
                        descricao  = f'Vistoria combinada com a reserva de {data.strftime("%d/%m/%Y")} (mesma unidade, dias seguidos) — sem vistoria própria',
                        usuario    = usuario
                    ))
                    db.session.add(Historico(
                        reserva_id = reserva.id,
                        descricao  = f'Revistoria remarcada para {(seguinte.data_festa + timedelta(days=1)).strftime("%d/%m/%Y")} — festa combinada com {seguinte.data_festa.strftime("%d/%m/%Y")}',
                        usuario    = usuario
                    ))

            # --- Registrar uso da regra (se isento) ---
            if regra_prec and isento and not festa_cond:
                ano_atual = data.year
                registro_anual = ReservaAnualUnidade(
                    condominio_id        = condominio.id,
                    salao_id             = salao_id,
                    apartamento          = apartamento_val.strip().upper(),
                    ano                  = ano_atual,
                    reserva_id           = reserva.id,
                    valor_opcional_usado = usar_opcional,
                )
                db.session.add(registro_anual)

                desc_historico = (
                    f'Reserva isenta — {regra_prec.descricao or "Regra especial"} '
                    f'({contagem_atual + 1}ª/{regra_prec.limite_reservas} do ano {ano_atual})'
                )
                if usar_opcional:
                    desc_historico += f' | Adicional: R$ {regra_prec.valor_opcional:.2f}'
                db.session.add(Historico(
                    reserva_id = reserva.id,
                    descricao  = desc_historico,
                    usuario    = usuario
                ))

            reserva_vistoria_hoje = Reserva.query.filter(
                Reserva.salao_id == salao_id,
                Reserva.data_vistoria == data,
                Reserva.vistoria == True
            ).filter(Reserva.status != 'cancelado').first()

            if reserva_vistoria_hoje:
                reserva_vistoria_hoje.data_vistoria = reserva_vistoria_hoje.data_festa

                db.session.add(Historico(
                    reserva_id = reserva_vistoria_hoje.id,
                    descricao  = f'Vistoria remarcada de {data.strftime("%d/%m/%Y")} para {reserva_vistoria_hoje.data_festa.strftime("%d/%m/%Y")}',
                    usuario    = usuario
                ))

            db.session.add(Historico(
                reserva_id = reserva.id,
                descricao  = f'Reserva criada com status {reserva.status}' + (f' (vistoria em {reserva.data_vistoria.strftime("%d/%m/%Y")})' if tem_vistoria else ''),
                usuario    = usuario
            ))

            if credito and not festa_cond:
                apt = apartamento_val
                credito_apt = Credito.query.filter_by(
                    condominio_id = condominio.id,
                    salao_id      = salao_id,
                    apartamento   = apt,
                    usado         = False
                ).order_by(Credito.criado_em.asc()).first()

                if credito_apt:
                    credito_apt.usado      = True
                    credito_apt.reserva_id = reserva.id

                    obs_credito = f'[Crédito R$ {credito_apt.valor:.2f}]'
                    if credito_apt.observacao:
                        obs_credito += f' {credito_apt.observacao}'
                    reserva.observacoes = (
                        f'{reserva.observacoes}\n{obs_credito}'.strip()
                        if reserva.observacoes else obs_credito
                    )

                    db.session.add(Historico(
                        reserva_id = reserva.id,
                        descricao  = f'Crédito de R$ {credito_apt.valor:.2f} aplicado automaticamente',
                        usuario    = usuario
                    ))

            db.session.commit()

            log_audit(
                session.get('usuario_id'), 'criar_reserva', 'reservas', reserva.id,
                dados_depois={
                    'nome_solicitante': '[alterado]' if reserva.nome_solicitante else None,
                    'apartamento':      '[alterado]' if reserva.apartamento else None,
                    'data_festa':       reserva.data_festa.isoformat(),
                    'salao_id':         reserva.salao_id,
                    'status':           reserva.status,
                }
            )
        except IntegrityError as e:
            db.session.rollback()
            if 'ix_reserva_dia_inteiro_unico' in str(e.orig):
                logging.warning(f"Conflito de reserva (índice único) salao_id={salao_id} data={data}: {e}")
                flash('Essa data acabou de ser reservada por outra pessoa. Atualize a página.', 'danger')
            else:
                logging.error(f"IntegrityError ao criar reserva: {e}")
                flash('Erro ao salvar a reserva. Tente novamente.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=data,
                credito=credito, horarios_disponiveis=horarios_disponiveis,
                vistoria_bloqueada=vistoria_bloqueada)
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erro ao criar reserva: {e}")
            flash('Erro ao salvar a reserva. Tente novamente.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=data,
                credito=credito, horarios_disponiveis=horarios_disponiveis,
                vistoria_bloqueada=vistoria_bloqueada)

        return redirect(url_for('condominios.ver_condominio', id=condominio.id,
            mes=data.month, ano=data.year))

    return render_template('formulario.html',
        salao=salao, condominio=condominio, data_festa=data,
        credito=credito, horarios_disponiveis=horarios_disponiveis,
        vistoria_bloqueada=vistoria_bloqueada,
        regra_preview=regra_preview,
        regra_isenta_preview=regra_isenta_preview)

@reservas_bp.route('/reserva/<int:id>')
@login_required
def ver_reserva(id):
    reserva = Reserva.query.get_or_404(id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    return redirect(url_for('reservas.ver_dia',
        condominio_id=reserva.salao.condominio_id,
        data_festa=reserva.data_festa.isoformat()))


@reservas_bp.route('/reserva/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_reserva(id):
    reserva    = Reserva.query.get_or_404(id)
    salao      = reserva.salao
    condominio = salao.condominio
    _garantir_posse_fiscal(condominio.id, reserva)
    usuario    = session.get('usuario_login', session.get('usuario_nome', ''))
    horarios_disponiveis = [h.strip() for h in (salao.horarios or '').split(',') if h.strip()]
    saloes = Salao.query.filter_by(condominio_id=condominio.id).order_by(Salao.nome).all()

    if request.method == 'POST':
        festa_cond = 'festa_condominio' in request.form

        if not festa_cond:
            erros = _validar_reserva(request.form, reserva.data_festa)
            if erros:
                for erro in erros:
                    flash(erro, 'danger')
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                    reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                    saloes=saloes)

        try:
            novo_salao_id = int(request.form.get('salao_id') or reserva.salao_id)
        except (ValueError, TypeError):
            novo_salao_id = reserva.salao_id

        salao_nome_antigo = salao.nome
        if novo_salao_id != reserva.salao_id:
            novo_salao = Salao.query.get(novo_salao_id)
            if not novo_salao or novo_salao.condominio_id != condominio.id:
                flash('Salão selecionado é inválido.', 'danger')
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                    reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                    saloes=saloes)
            conflito_salao = Reserva.query.filter_by(
                salao_id=novo_salao_id, data_festa=reserva.data_festa
            ).filter(Reserva.status != 'cancelado').filter(Reserva.id != id).first()
            if conflito_salao:
                flash(f'O salão "{novo_salao.nome}" já está reservado para {reserva.data_festa.strftime("%d/%m/%Y")}.', 'danger')
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                    reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                    saloes=saloes)
            salao = novo_salao
            horarios_disponiveis = [h.strip() for h in (salao.horarios or '').split(',') if h.strip()]

        campos_antigos = {
            'status':           reserva.status,
            'nome_solicitante': reserva.nome_solicitante,
            'apartamento':      reserva.apartamento,
            'vistoria':         reserva.vistoria,
            'salao_id':         reserva.salao_id,
        }

        horario_escolhido = request.form.get('horario', reserva.horario or '').strip()
        if (horario_escolhido and horario_escolhido != (reserva.horario or '')
                and horarios_disponiveis and horario_escolhido not in horarios_disponiveis):
            flash('Formato de horário inválido.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                saloes=saloes)

        if salao.tipo == 'horario_fixo' and horario_escolhido and horario_escolhido != reserva.horario:
            conflito_horario = Reserva.query.filter_by(
                salao_id=salao.id, data_festa=reserva.data_festa, horario=horario_escolhido
            ).filter(Reserva.status != 'cancelado').filter(Reserva.id != id).first()
            if conflito_horario:
                return render_template('formulario.html',
                    salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                    reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                    saloes=saloes, erro_conflito=horario_escolhido)

        try:
            # [FIX] mesma correção de nova_reserva: tem_vistoria passa a
            # checar emite_termo + fiscal_proprio_seg_sab (só domingo, se o
            # condomínio tiver fiscal próprio seg-sáb). Ver
            # Condominio.precisa_vistoria_em() em app/models.py.
            antecipa_desejada       = (not festa_cond) and ('vistoria' in request.form)
            data_vistoria_candidata = (reserva.data_festa - timedelta(days=1)) if antecipa_desejada else reserva.data_festa
            tem_vistoria  = (not festa_cond) and condominio.precisa_vistoria_em(data_vistoria_candidata)
            data_vistoria = data_vistoria_candidata if tem_vistoria else None
            hora_vistoria_raw = request.form.get('hora_vistoria', '').strip()
            hora_vistoria     = hora_vistoria_raw if (
                tem_vistoria and
                re.match(r'^([01]\d|2[0-3]):[0-5]\d$', hora_vistoria_raw)
            ) else None

            if festa_cond:
                reserva.nome_solicitante = None
                reserva.contato          = None
                reserva.apartamento      = None
            else:
                reserva.nome_solicitante = request.form['nome_solicitante'].strip()
                reserva.contato          = request.form.get('contato', '').strip()
                reserva.apartamento      = request.form['apartamento'].strip()

            reserva.horario          = horario_escolhido
            novo_status = request.form.get('status', reserva.status)
            reserva.status           = novo_status if novo_status in {'confirmado', 'pendente', 'cancelado'} else reserva.status
            reserva.vistoria         = tem_vistoria
            reserva.data_vistoria    = data_vistoria
            reserva.hora_vistoria    = hora_vistoria
            reserva.zelador          = _zelador_val(salao, request.form, reserva.data_festa)
            reserva.surpresa         = 'surpresa' in request.form
            reserva.festa_condominio = festa_cond
            reserva.observacoes      = sanitize_text(request.form.get('observacoes', ''), 500)
            if novo_salao_id != campos_antigos['salao_id']:
                reserva.salao_id = novo_salao_id
                reserva.bloqueia_dia_todo = (salao.tipo == 'dia_inteiro')

            alteracoes = []
            if campos_antigos['salao_id'] != reserva.salao_id:
                alteracoes.append(f'salão: {salao_nome_antigo} → {salao.nome}')
            if campos_antigos['status'] != reserva.status:
                alteracoes.append(f'status: {campos_antigos["status"]} → {reserva.status}')
            if campos_antigos['nome_solicitante'] != reserva.nome_solicitante:
                alteracoes.append('nome do solicitante alterado')
            if campos_antigos['apartamento'] != reserva.apartamento:
                alteracoes.append(f'apartamento: {campos_antigos["apartamento"]} → {reserva.apartamento}')
            if campos_antigos['vistoria'] != reserva.vistoria:
                if tem_vistoria:
                    alteracoes.append(f'vistoria ativada ({reserva.data_vistoria.strftime("%d/%m/%Y")})')
                else:
                    alteracoes.append('vistoria removida')

            if alteracoes:
                db.session.add(Historico(
                    reserva_id = reserva.id,
                    descricao  = 'Editado: ' + ', '.join(alteracoes),
                    usuario    = usuario
                ))

            db.session.commit()

            log_audit(
                session.get('usuario_id'), 'editar_reserva', 'reservas', reserva.id,
                dados_antes={
                    'status':           campos_antigos['status'],
                    'nome_solicitante': '[alterado]' if campos_antigos['nome_solicitante'] else None,
                    'apartamento':      '[alterado]' if campos_antigos['apartamento'] else None,
                    'vistoria':         campos_antigos['vistoria'],
                    'salao_id':         campos_antigos['salao_id'],
                },
                dados_depois={
                    'nome_solicitante': '[alterado]' if reserva.nome_solicitante else None,
                    'apartamento':      '[alterado]' if reserva.apartamento else None,
                    'status':           reserva.status,
                    'vistoria':         reserva.vistoria,
                }
            )
        except IntegrityError as e:
            db.session.rollback()
            if 'ix_reserva_dia_inteiro_unico' in str(e.orig):
                flash('Esse salão já está reservado para essa data. Atualize a página.', 'danger')
            else:
                logging.error(f"IntegrityError ao editar reserva: {e}")
                flash('Erro ao salvar as alterações. Tente novamente.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                saloes=saloes)
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erro ao editar reserva: {e}")
            flash('Erro ao salvar as alterações. Tente novamente.', 'danger')
            return render_template('formulario.html',
                salao=salao, condominio=condominio, data_festa=reserva.data_festa,
                reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
                saloes=saloes)

        return redirect(url_for('reservas.ver_reserva', id=id))

    return render_template('formulario.html',
        salao=salao, condominio=condominio, data_festa=reserva.data_festa,
        reserva=reserva, credito=None, horarios_disponiveis=horarios_disponiveis,
        saloes=saloes)


@reservas_bp.route('/reserva/<int:id>/cancelar', methods=['POST'])
@login_required
def cancelar_reserva(id):
    reserva               = Reserva.query.get_or_404(id)
    condominio_id         = reserva.salao.condominio_id
    _garantir_posse_fiscal(condominio_id, reserva)
    usuario               = session.get('usuario_login', session.get('usuario_nome', ''))
    mes                   = reserva.data_festa.month
    ano                   = reserva.data_festa.year

    observacao_cancelamento = sanitize_text(request.form.get('observacao_cancelamento', ''), 500)

    try:
        reserva.status                  = 'cancelado'
        reserva.cancelado_em            = datetime.now(timezone.utc)
        reserva.cancelado_por           = usuario
        reserva.observacao_cancelamento = observacao_cancelamento
        descricao_historico = 'Reserva cancelada'
        if observacao_cancelamento:
            descricao_historico += f' — Obs: {observacao_cancelamento}'
        db.session.add(Historico(
            reserva_id = reserva.id,
            descricao  = descricao_historico,
            usuario    = usuario
        ))

        # Libera cota anual se reserva era isenta
        registro_anual = ReservaAnualUnidade.query.filter_by(reserva_id=id).first()
        if registro_anual:
            db.session.delete(registro_anual)
            db.session.add(Historico(
                reserva_id = reserva.id,
                descricao  = f'Cota anual liberada por cancelamento (apto {registro_anual.apartamento}, ano {registro_anual.ano})',
                usuario    = usuario
            ))

        # --- Desfaz vistoria combinada, se essa reserva fizer parte de uma ---
        # Caso A: cancelou o 2º dia (o "filho") — a revistoria da dona volta
        # a ser o padrão D+1, já que não tem mais festa no dia seguinte.
        if reserva.vistoria_pareada_com_id:
            dona = Reserva.query.get(reserva.vistoria_pareada_com_id)
            if dona and dona.status != 'cancelado':
                dona.data_revistoria_override = None
                db.session.add(Historico(
                    reserva_id = dona.id,
                    descricao  = f'Revistoria voltou ao padrão (D+1) — reserva combinada de {reserva.data_festa.strftime("%d/%m/%Y")} foi cancelada',
                    usuario    = usuario
                ))

        # Caso B: cancelou o 1º dia (a "dona") e o 2º dia continua ativo —
        # promove o filho de volta a uma reserva normal, com vistoria própria.
        # Sem isso a festa do 2º dia ficaria sem nenhuma vistoria agendada.
        filho = Reserva.query.filter_by(
            vistoria_pareada_com_id=reserva.id
        ).filter(Reserva.status != 'cancelado').first()
        if filho:
            filho.vistoria               = reserva.salao.condominio.precisa_vistoria_em(filho.data_festa)
            filho.data_vistoria          = filho.data_festa if filho.vistoria else None
            filho.vistoria_pareada_com_id = None
            db.session.add(Historico(
                reserva_id = filho.id,
                descricao  = f'Vistoria própria restaurada — reserva combinada de {reserva.data_festa.strftime("%d/%m/%Y")} foi cancelada',
                usuario    = usuario
            ))

        db.session.commit()

        log_audit(
            session.get('usuario_id'), 'cancelar_reserva', 'reservas', reserva.id,
            dados_depois={
                'status':                  'cancelado',
                'cancelado_em':            reserva.cancelado_em.isoformat() if reserva.cancelado_em else None,
                'cancelado_por':           reserva.cancelado_por,
                'observacao_cancelamento': reserva.observacao_cancelamento,
            }
        )
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erro ao cancelar reserva {id}: {e}")
        flash('Erro ao cancelar a reserva. Tente novamente.', 'error')

    # [FEATURE] fiscal (ou coordenador) cancela pelo painel mobile (morador
    # desiste no local) — não faz sentido mandar pra tela de condomínio (é
    # a visão do operador/admin, nem aparece direito no celular). Volta pro
    # painel dele.
    if session.get('usuario_perfil') == 'fiscal_fixo':
        return redirect(url_for('fiscal_fixo.portal'))
    if session.get('usuario_perfil') in ('fiscal', 'coordenador'):
        return redirect(url_for('vistorias.painel_fiscal'))

    return redirect(url_for('condominios.ver_condominio',
        id=condominio_id, mes=mes, ano=ano))


@reservas_bp.route('/dia/<int:condominio_id>/<string:data_festa>')
@login_required
def ver_dia(condominio_id, data_festa):
    condominio = Condominio.query.get_or_404(condominio_id)
    data       = _parse_data(data_festa)
    saloes     = condominio.saloes

    reservas_dia = (
        Reserva.query.join(Salao)
        .filter(Salao.condominio_id == condominio_id)
        .filter(Reserva.data_festa == data)
        .all()
    )

    saloes_ocupados = {r.salao_id for r in reservas_dia if r.status != 'cancelado'}

    saloes_ocupados_expandido = set(saloes_ocupados)
    for salao in condominio.saloes:
        if salao.id in saloes_ocupados and salao.grupo:
            if salao.combo:
                for s in condominio.saloes:
                    if s.grupo == salao.grupo:
                        saloes_ocupados_expandido.add(s.id)
            else:
                for s in condominio.saloes:
                    if s.grupo == salao.grupo and s.combo:
                        saloes_ocupados_expandido.add(s.id)
    saloes_ocupados = saloes_ocupados_expandido

    horarios_ocupados = {}
    for r in reservas_dia:
        if r.status != 'cancelado' and r.horario:
            horarios_ocupados.setdefault(r.salao_id, set()).add(r.horario)

    return render_template('detalhe_dia.html',
        condominio=condominio,
        data_festa=data,
        saloes=saloes,
        reservas_dia=reservas_dia,
        saloes_ocupados=saloes_ocupados,
        horarios_ocupados=horarios_ocupados)


@reservas_bp.route('/api/morador/<apartamento>/<int:condominio_id>')
@login_required
def api_morador(apartamento, condominio_id):
    _garantir_posse_fiscal(condominio_id)
    # Sanitiza input — apenas alfanumérico e hífen, 1-20 chars
    if not apartamento or len(apartamento) > 20 or not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]*$', apartamento):
        return jsonify({}), 400

    apto_norm = re.sub(r'\s+', '', apartamento.strip().upper())

    # apartamento é @hybrid_property encriptado — não pode ser filtrado no SQL.
    # Carrega as últimas 500 reservas do condomínio e filtra em Python.
    #
    # Não filtra por status != 'cancelado' de propósito: cancelar uma festa não
    # invalida o nome/telefone do morador. Se a última reserva daquele apto foi
    # cancelada e a pessoa reservou de novo depois, o autofill precisa achar do
    # mesmo jeito — o dado de contato continua válido independente do status da
    # festa anterior.
    candidatas = (
        Reserva.query
        .join(Salao)
        .filter(Salao.condominio_id == condominio_id)
        .filter(Reserva.festa_condominio == False)
        .order_by(Reserva.data_festa.desc())
        .limit(500)
        .all()
    )

    for r in candidatas:
        apto_r = re.sub(r'\s+', '', (r.apartamento or '').strip().upper())
        if apto_r == apto_norm:
            return jsonify({'nome': r.nome_solicitante or '', 'contato': r.contato or ''})

    return jsonify({'erro': 'Não encontrado'}), 404


@reservas_bp.route('/reserva/<int:reserva_id>/marcar-impresso', methods=['POST'])
@login_required
def marcar_como_impresso(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    try:
        reserva.impresso = True
        db.session.commit()
        return jsonify({'status': 'success'}), 200
    except Exception:
        db.session.rollback()
        logging.exception('Erro ao marcar reserva %s como impressa', reserva_id)
        return jsonify({'status': 'error'}), 500


@reservas_bp.route('/api/reserva-anual-status/<int:salao_id>/<string:apartamento>/<int:condominio_id>')
@login_required
def api_reserva_anual_status(salao_id, apartamento, condominio_id):
    if not apartamento or len(apartamento) > 20 or not re.match(r'^[A-Za-z0-9][A-Za-z0-9\-]*$', apartamento):
        return jsonify({'isento': False}), 400

    regra, isento, contagem = _checar_regra_precificacao(salao_id, condominio_id, apartamento)
    if regra is None:
        return jsonify({'isento': False}), 200

    return jsonify({
        'isento':         isento,
        'contagem':       contagem,
        'limite':         regra.limite_reservas,
        'valor_opcional': float(regra.valor_opcional) if regra.valor_opcional else None,
        'descricao':      regra.descricao,
    })


@reservas_bp.route('/api/reservas/<int:id>/gep_obs', methods=['PATCH'])
@login_required
def gep_obs(id):
    reserva = Reserva.query.get_or_404(id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    try:
        data = request.get_json(force=True) or {}
        logging.debug('gep_obs id=%s payload=%s', id, data)
        if 'gerado' in data:
            reserva.boleto_gerado  = bool(data['gerado'])
        if 'enviado' in data:
            reserva.boleto_enviado = bool(data['enviado'])
        if 'pago' in data:
            reserva.boleto_pago    = bool(data['pago'])
        if 'observacoes' in data:
            reserva.observacoes = sanitize_text(str(data['observacoes']), 500)
        if 'hora_vistoria' in data:
            val = str(data['hora_vistoria']).strip()[:5]
            reserva.hora_vistoria = val if re.match(r'^([01]\d|2[0-3]):[0-5]\d$', val) else None
        db.session.commit()
        logging.debug('gep_obs id=%s committed: G=%s E=%s P=%s obs=%r',
                      id, reserva.boleto_gerado, reserva.boleto_enviado,
                      reserva.boleto_pago, reserva.observacoes)
        return jsonify({'ok': True}), 200
    except Exception:
        db.session.rollback()
        logging.exception('Erro ao atualizar GEP/obs reserva id=%s', id)
        return jsonify({'error': 'Erro interno'}), 500


@reservas_bp.route('/api/reservas/<int:id>/termo-comunicado', methods=['PATCH'])
@login_required
def editar_termo_comunicado(id):
    # [TESTE] overrides manuais e raros de termo/comunicado — não altera a
    # lógica automática (vistoria/revistoria/PII), só permite substituir o
    # valor calculado quando o caso foge do padrão. Ver models.Reserva.
    reserva = Reserva.query.get_or_404(id)
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)
    try:
        data = request.get_json(force=True) or {}
        dados_antes = {
            'data_vistoria':             reserva.data_vistoria.isoformat() if reserva.data_vistoria else None,
            'data_revistoria_override':  reserva.data_revistoria_override.isoformat() if reserva.data_revistoria_override else None,
            'motivo_festa':              reserva.motivo_festa,
            'comunicado_texto_override': reserva.comunicado_texto_override,
        }

        if 'data_vistoria' in data:
            val = str(data['data_vistoria']).strip()
            reserva.data_vistoria = date.fromisoformat(val) if val else None

        if 'data_revistoria_override' in data:
            val = str(data['data_revistoria_override']).strip()
            reserva.data_revistoria_override = date.fromisoformat(val) if val else None

        if 'motivo_festa' in data:
            reserva.motivo_festa = sanitize_text(str(data['motivo_festa']), 200)

        if 'comunicado_texto_override' in data:
            reserva.comunicado_texto_override = sanitize_text(str(data['comunicado_texto_override']), 500)

        db.session.commit()

        log_audit(
            session.get('usuario_id'), 'editar_termo_comunicado', 'reservas', id,
            dados_antes=dados_antes,
            dados_depois={
                'data_vistoria':             reserva.data_vistoria.isoformat() if reserva.data_vistoria else None,
                'data_revistoria_override':  reserva.data_revistoria_override.isoformat() if reserva.data_revistoria_override else None,
                'motivo_festa':              reserva.motivo_festa,
                'comunicado_texto_override': reserva.comunicado_texto_override,
            }
        )

        return jsonify({
            'ok': True,
            'data_vistoria':             reserva.data_vistoria.isoformat() if reserva.data_vistoria else None,
            'dia_revistoria':            reserva.dia_revistoria.isoformat() if reserva.dia_revistoria else None,
            'motivo_festa':              reserva.motivo_festa,
            'comunicado_texto_override': reserva.comunicado_texto_override,
        }), 200
    except ValueError:
        db.session.rollback()
        return jsonify({'error': 'Data inválida'}), 400
    except Exception:
        db.session.rollback()
        logging.exception('Erro ao editar termo/comunicado reserva id=%s', id)
        return jsonify({'error': 'Erro interno'}), 500