import base64
import io
import re
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort, send_file
from sqlalchemy.orm import joinedload, selectinload
from app import db, csrf
from app.models import (
    Reserva, Salao, Condominio, VistoriaTermo, VistoriaItem, VistoriaFoto,
    ItemProblema, Usuario, log_audit, sanitize_text,
)
from app.routes.auth import login_required
from app.routes.fiscal_fixo import _garantir_posse_fiscal
import pytz

vistorias_bp = Blueprint('vistorias', __name__)

_MOMENTOS       = ('vistoria', 'revistoria')
_ASSINATURA_RE  = re.compile(r'^data:image/png;base64,')
_MIN_ASSIN_LEN  = 300  # canvas em branco gera PNG bem menor que isso

_FOTO_RE       = re.compile(r'^data:image/(png|jpe?g);base64,')
_MAX_FOTOS     = 6
# [AJUSTE] pedido do Viny: dar mais folga pro fiscal anexar foto sem
# recompressão agressiva. Orçamento por foto dobrado de 1MB -> 2MB
# decodificado. Pior caso: 6 fotos x 2MB = 12MB decodificado (~16MB em
# base64, inflação de ~33%) + 2 assinaturas (poucos KB cada) ~= 16-17MB de
# corpo por request — MAX_CONTENT_LENGTH/MAX_FORM_MEMORY_SIZE subiram pra
# 20MB (app/__init__.py) pra cobrir isso com margem.
# Memória: com Gunicorn -w 2 --threads 4 (8 slots concorrentes), o pior caso
# teórico de TODOS os slots recebendo o payload máximo ao mesmo tempo é
# ~8 x 17MB ~= 136MB transitórios — aceitável pro uso real (poucos fiscais
# enviando vistoria ao mesmo tempo) e o buffer não persiste: vira bytes no
# Postgres (LargeBinary) e é liberado ao fim do request.
_MAX_FOTO_LEN  = 2 * 1024 * 1024


def _hoje_br():
    return datetime.now(pytz.timezone('America/Sao_Paulo')).date()


_DIAS_SEMANA = [
    'Segunda-feira', 'Terça-feira', 'Quarta-feira',
    'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo',
]


def _fmt_data_extenso(d):
    # strftime('%A') depende do locale do servidor (Railway roda em C/en_US,
    # sem locale pt_BR instalado) — em vez de depender disso, monta na mão.
    return f'{_DIAS_SEMANA[d.weekday()]}, {d.strftime("%d/%m/%Y")}'


def _fmt_br(dt_utc_naive):
    if not dt_utc_naive:
        return None
    br = pytz.utc.localize(dt_utc_naive).astimezone(pytz.timezone('America/Sao_Paulo'))
    return br.strftime('%d/%m/%Y às %H:%M')


def _redirect_pos_vistoria():
    perfil = session.get('usuario_perfil')
    if perfil in ('fiscal', 'coordenador'):
        return redirect(url_for('vistorias.painel_fiscal'))
    if perfil == 'fiscal_fixo':
        return redirect(url_for('fiscal_fixo.portal'))
    return redirect(url_for('relatorios.relatorio_semanal'))


def _extrai_assinatura(campo, form):
    raw = form.get(campo, '').strip()
    if not raw or not _ASSINATURA_RE.match(raw):
        return None
    b64 = raw.split(',', 1)[1]
    try:
        decoded = base64.b64decode(b64, validate=True)
    except Exception:
        return None
    if len(decoded) < _MIN_ASSIN_LEN:
        return None
    return raw


def _extrai_fotos(form):
    # [TESTE] fotos de problema — anexadas ao termo (não a um item específico,
    # a pedido explícito: "a foto fica vinculada ao termo"). Cliente já
    # redimensiona/comprime antes de mandar; aqui só valida e limita.
    fotos = []
    for raw in form.getlist('foto_b64')[:_MAX_FOTOS]:
        raw = (raw or '').strip()
        if not raw or not _FOTO_RE.match(raw):
            continue
        try:
            decoded = base64.b64decode(raw.split(',', 1)[1], validate=True)
        except Exception:
            continue
        if not decoded or len(decoded) > _MAX_FOTO_LEN:
            continue
        fotos.append(decoded)
    return fotos


def _saloes_alvo(reserva):
    # Mesma lógica de resolução de combo/grupo usada em gerar_pdf_termos
    # (app/routes/relatorios.py): salão combo não tem inventário próprio — o
    # inventário fica cadastrado nos salões individuais que compõem o grupo.
    salao = reserva.salao
    if salao.combo and salao.grupo:
        individuais = Salao.query.filter_by(
            condominio_id=salao.condominio_id, grupo=salao.grupo, combo=False
        ).all()
        return individuais if individuais else [salao]
    return [salao]


def _regras_salao(condominio):
    # Mesmo conteúdo das regras impressas no termo (ver gerar_pdf_termos em
    # relatorios.py), em texto puro — sem as tags de ReportLab, que não fazem
    # sentido pra renderização HTML.
    regras_default = [
        'O Salão de Festas deve ser utilizado de tal forma a não perturbar a boa ordem '
        'do Edifício e o sossego dos demais condôminos (USO DE SOM ALTO), com observância '
        'de todas as normas da Convenção do Condomínio e do seu Regimento Interno.',
        'O Salão de Festas será utilizado por mim, minha família e convidados, de tal '
        'forma a não perturbar a boa ordem do Edifício e sossego dos demais condôminos, '
        'com observância de todas as normas da Convenção do Condomínio e do seu Regimento Interno.',
        'No prazo de 04 (quatro) horas após a sua utilização, o Salão de Festas será '
        'recolocado em suas condições originais de recebimento e perfeitamente limpo.',
        'Corre por minha integral responsabilidade todo e qualquer dano sofrido pelo '
        'revestimento (pintura, vidros e balcão), mesas, cadeiras e acessórios dos '
        'banheiros que guarnecem o referido Salão, durante o período de sua utilização.',
        f'Autorizo a administração do Condomínio do {condominio.nome} a reparar todo e '
        'qualquer dano ocasionado durante a utilização do Salão de Festas, bem como, '
        'acrescentar no boleto de pagamento da taxa condominial, o valor correspondente '
        'à recomposição dos bens.',
    ]
    regra_obj = condominio.regras[0] if condominio.regras else None
    regras_extras = (
        [l.strip() for l in regra_obj.texto.strip().split('\n') if l.strip()]
        if regra_obj else []
    )
    return regras_default + regras_extras


@vistorias_bp.route('/fiscal/painel')
@login_required
def painel_fiscal():
    hoje       = _hoje_br()
    amanha     = hoje + timedelta(days=1)
    usuario_id = session.get('usuario_id')
    eh_fiscal  = session.get('usuario_perfil') == 'fiscal'
    # [FEATURE] coordenador de fiscais — vê e reivindica vistoria de
    # qualquer fiscal, em qualquer dia (não só fim de semana). Painel
    # consolidado: mesma tela, sem filtro nenhum de rota.
    # [FEATURE] pedido do Viny: admin tem acesso a tudo, inclusive todo
    # privilégio de coordenador aqui dentro — trata os dois perfis igual
    # nesse contexto.
    eh_coordenador = session.get('usuario_perfil') in ('coordenador', 'admin')

    def _query_base():
        # [FIX] data_vistoria é gravado na criação da reserva com base no
        # emite_termo do condomínio NAQUELE momento. Se o admin desativar
        # emite_termo depois, a reserva antiga continua com data_vistoria
        # preenchido — sem esse filtro, o card aparecia pro fiscal mas dava
        # 404 ao abrir (preencher_vistoria recalcula tem_vistoria na hora,
        # vendo o emite_termo atual). Filtra aqui pra nunca listar o que não
        # pode ser aberto — mesma regra que o dashboard usa pra exibir.
        return (
            Reserva.query.join(Salao).join(Condominio)
            # [FIX] N+1 no painel (pior no consolidado do coordenador, que
            # lista o fim de semana inteiro): cada card lê salao.condominio,
            # condominio.fiscal, fiscal_responsavel, vistorias_termo e as
            # fotos de cada termo. Sem eager load isso vira dezenas de queries
            # por render. fotos/itens já são lazy='selectin' no model.
            .options(
                joinedload(Reserva.salao)
                    .joinedload(Salao.condominio)
                    .joinedload(Condominio.fiscal),
                joinedload(Reserva.fiscal_responsavel),
                selectinload(Reserva.vistorias_termo).selectinload(VistoriaTermo.fotos),
            )
            .filter(
                Reserva.status != 'cancelado',
                Condominio.emite_termo == True,
                # [FIX] festa do condomínio nunca tem vistoria/termo (regra de
                # negócio: tem_vistoria = not festa_condominio and emite_termo).
                Reserva.festa_condominio == False,
                # [FIX] condomínio com fiscal_fixo vinculado faz a própria
                # vistoria — nunca aparece no painel dos fiscais da Administradora.
                Condominio.fiscal_id.is_(None),
            )
        )

    def _aplicar_rota_fiscal(query, dia):
        if eh_coordenador:
            # Sem filtro nenhum — vê tudo, de todo mundo, todo dia.
            return query
        if not eh_fiscal:
            return query
        if dia.weekday() >= 5:
            # [FIX] fim de semana não tem designação fixa por condomínio
            # — só 2/3 fiscais de plantão, variável. Sem reivindicação,
            # mostra tudo; com reivindicação, só quem pegou.
            return query.filter(db.or_(
                Reserva.fiscal_responsavel_id == usuario_id,
                Reserva.fiscal_responsavel_id.is_(None),
            ))
        # Dia de semana: rota fixa por condomínio. Designado, ou sem
        # ninguém designado ainda (fallback pra não perder vistoria por
        # condomínio esquecido).
        return query.filter(db.or_(
            Condominio.fiscal_id.is_(None),
            Condominio.fiscal_id == usuario_id,
        ))

    # [TESTE] pedido do Viny: separar vistoria e revistoria em duas listas
    # (vistorias em cima, revistorias embaixo), em vez de um card genérico
    # que tentava mostrar as duas ações misturadas.
    def _vistorias_do_dia(dia):
        query = _query_base().filter(Reserva.data_vistoria == dia)
        if dia.weekday() != 6:
            # [FIX] mesmo problema de dado congelado do comentário em
            # _query_base, agora pro fiscal_proprio_seg_sab: reserva criada
            # ANTES do condomínio marcar "fiscal próprio" continua com
            # data_vistoria preenchido num dia de semana. Se hoje o
            # condomínio tem fiscal próprio e o dia não é domingo, não é da
            # Administradora mesmo que o campo antigo diga que sim — reconsulta a regra
            # atual, não confia só no que foi gravado na criação.
            query = query.filter(Condominio.fiscal_proprio_seg_sab == False)
        query = _aplicar_rota_fiscal(query, dia)
        return query.order_by(Reserva.hora_vistoria.asc().nullslast(), Condominio.nome).all()

    def _revistorias_do_dia(dia):
        # [FIX] revistoria é no dia seguinte à festa (data_festa + 1), não em
        # data_vistoria (que é sobre a vistoria ANTES da festa).
        #
        # [TESTE] fiscal próprio (seg-sáb) — pra condomínio com fiscal
        # próprio, revistoria só é da administradora se `dia` for domingo;
        # segunda-sábado o fiscal próprio cobre por conta dele, então nem
        # entra na lista da Administradora.
        #
        # [TESTE] data_revistoria_override — caso raro (festa em dias
        # seguidos) desloca a revistoria pra outro dia; sem isso, essa query
        # nunca achava a reserva no dia certo (só olhava data_festa+1) e o
        # fiscal não via a revistoria remarcada na rota dele.
        dia_da_festa  = dia - timedelta(days=1)
        condicao_data = db.or_(
            db.and_(Reserva.data_revistoria_override.is_(None), Reserva.data_festa == dia_da_festa),
            Reserva.data_revistoria_override == dia,
        )
        if dia.weekday() == 6:
            condicao = condicao_data
        else:
            condicao = db.and_(condicao_data, Condominio.fiscal_proprio_seg_sab == False)
        query = _query_base().filter(condicao)
        query = _aplicar_rota_fiscal(query, dia)
        return query.order_by(Condominio.nome).all()

    # [FEATURE] pedido do Viny: coordenador precisa ver o próximo fim de
    # semana inteiro (não só hoje/amanhã) pra poder designar fiscal com
    # antecedência, mesmo no meio da semana. Calcula o próximo sábado (hoje
    # mesmo, se hoje já for sábado) e o domingo seguinte.
    dados_fds = {}
    if eh_coordenador:
        dias_ate_sabado = (5 - hoje.weekday()) % 7
        sabado  = hoje + timedelta(days=dias_ate_sabado)
        domingo = sabado + timedelta(days=1)
        dados_fds = dict(
            vistorias_sabado=_vistorias_do_dia(sabado),
            revistorias_sabado=_revistorias_do_dia(sabado),
            vistorias_domingo=_vistorias_do_dia(domingo),
            revistorias_domingo=_revistorias_do_dia(domingo),
            sabado_extenso=_fmt_data_extenso(sabado),
            domingo_extenso=_fmt_data_extenso(domingo),
        )

    # [FEATURE] lista de fiscais pra designação manual (coordenador/admin) —
    # só carrega quando faz sentido usar (mesmo perfil que já enxerga o
    # bloco de designação no card).
    fiscais = (
        Usuario.query.filter_by(perfil='fiscal', ativo=True).order_by(Usuario.nome).all()
        if eh_coordenador else []
    )

    return render_template('painel_fiscal.html',
        vistorias_hoje=_vistorias_do_dia(hoje),
        revistorias_hoje=_revistorias_do_dia(hoje),
        vistorias_amanha=_vistorias_do_dia(amanha),
        revistorias_amanha=_revistorias_do_dia(amanha),
        hoje=hoje, hoje_extenso=_fmt_data_extenso(hoje),
        amanha_extenso=_fmt_data_extenso(amanha),
        eh_fds_hoje=eh_fiscal and hoje.weekday() >= 5,
        eh_fds_amanha=eh_fiscal and amanha.weekday() >= 5,
        eh_coordenador=eh_coordenador,
        eh_admin_visitando=session.get('usuario_perfil') == 'admin',
        usuario_id=usuario_id,
        fiscais=fiscais,
        **dados_fds)


@vistorias_bp.route('/vistoria/<int:reserva_id>/pegar', methods=['POST'])
@login_required
def pegar_vistoria(reserva_id):
    # [TESTE] reivindicação de fim de semana — trava a vistoria só pra quem
    # pegou. Não valida dia da semana de propósito: se alguém pegar errado,
    # basta liberar; não é uma trava de segurança, é organização de rota.
    reserva        = Reserva.query.get_or_404(reserva_id)
    usuario_id     = session.get('usuario_id')
    # [FEATURE] coordenador pode reivindicar de qualquer fiscal, mesmo já
    # pega por outro — é o próprio propósito do privilégio, não uma trava
    # de segurança sendo furada.
    eh_coordenador = session.get('usuario_perfil') in ('coordenador', 'admin')
    if reserva.fiscal_responsavel_id and reserva.fiscal_responsavel_id != usuario_id and not eh_coordenador:
        flash('Essa vistoria já foi pega por outro fiscal.', 'danger')
    else:
        reserva.fiscal_responsavel_id = usuario_id
        try:
            db.session.commit()
            flash('Vistoria adicionada à sua rota.', 'success')
        except Exception:
            db.session.rollback()
            logging.exception('Falha ao reivindicar vistoria %s', reserva_id)
            flash('Erro ao pegar a vistoria. Tente novamente.', 'error')
    return redirect(url_for('vistorias.painel_fiscal'))


@vistorias_bp.route('/vistoria/<int:reserva_id>/designar', methods=['POST'])
@login_required
def designar_fiscal(reserva_id):
    # [FEATURE] pedido do Viny: substitui "Assumir pra mim" — em vez do
    # coordenador só reivindicar pra si mesmo, ele (ou o admin) escolhe
    # qual fiscal de plantão vai cobrir essa vistoria/revistoria. Cobre o
    # caso "aconteceu algo" (fiscal escalado não pode ir e alguém precisa
    # realocar outro fiscal em cima).
    if session.get('usuario_perfil') not in ('coordenador', 'admin'):
        abort(403)

    reserva  = Reserva.query.get_or_404(reserva_id)
    raw      = request.form.get('fiscal_id', '')
    fiscal   = Usuario.query.filter_by(id=int(raw), perfil='fiscal', ativo=True).first() if raw.isdigit() else None
    if not fiscal:
        flash('Selecione um fiscal válido.', 'danger')
        return redirect(url_for('vistorias.painel_fiscal'))

    reserva.fiscal_responsavel_id = fiscal.id
    try:
        db.session.commit()
        flash(f'{fiscal.nome} designado pra essa vistoria.', 'success')
    except Exception:
        db.session.rollback()
        logging.exception('Falha ao designar fiscal na vistoria %s', reserva_id)
        flash('Erro ao designar fiscal. Tente novamente.', 'error')
    return redirect(url_for('vistorias.painel_fiscal'))


@vistorias_bp.route('/vistoria/<int:reserva_id>/liberar', methods=['POST'])
@login_required
def liberar_vistoria(reserva_id):
    reserva        = Reserva.query.get_or_404(reserva_id)
    usuario_id     = session.get('usuario_id')
    # [FEATURE] coordenador libera vistoria de qualquer fiscal (reorganizar
    # rota alheia), não só a própria.
    eh_coordenador = session.get('usuario_perfil') in ('coordenador', 'admin')
    if reserva.fiscal_responsavel_id == usuario_id or eh_coordenador:
        reserva.fiscal_responsavel_id = None
        try:
            db.session.commit()
            flash('Vistoria liberada pra outros fiscais.', 'success')
        except Exception:
            db.session.rollback()
            logging.exception('Falha ao liberar vistoria %s', reserva_id)
            flash('Erro ao liberar a vistoria. Tente novamente.', 'error')
    return redirect(url_for('vistorias.painel_fiscal'))


@vistorias_bp.route('/vistoria/<int:reserva_id>/<string:momento>', methods=['GET', 'POST'])
@login_required
@csrf.exempt
def preencher_vistoria(reserva_id, momento):
    # [FIX] CSRF exempt SÓ neste endpoint: a vistoria offline reenvia o POST
    # horas depois (fila no IndexedDB), quando o token CSRF de 8h já pode ter
    # vencido — a recusa mandava a vistoria pra uma "fila fantasma" que nunca
    # sincronizava. A proteção continua: @login_required exige a sessão e
    # SESSION_COOKIE_SAMESITE='Lax' bloqueia POST cross-site com o cookie.
    # Os outros POSTs do fiscal (pegar/liberar/cancelar) mantêm CSRF normal.
    if momento not in _MOMENTOS:
        abort(404)

    reserva = Reserva.query.get_or_404(reserva_id)
    salao      = reserva.salao
    condominio = salao.condominio

    # [TESTE] fiscal próprio (seg-sáb) — vistoria (pré-festa) é campo
    # persistido, mas não confia só em "is not None": reserva criada ANTES
    # do condomínio marcar fiscal_proprio_seg_sab pode ter data_vistoria
    # preenchido num dia que hoje não é mais da Administradora (dado congelado, mesmo
    # problema histórico do emite_termo). Sempre reconsulta a regra ATUAL via
    # Condominio.precisa_vistoria_em(). Já revistoria nunca foi persistida —
    # calcula direto se o dia (data_festa + 1) é da administradora.
    if momento == 'vistoria':
        tem_vistoria = condominio.precisa_vistoria_em(reserva.data_vistoria)
    else:
        tem_vistoria = (
            not reserva.festa_condominio
            and condominio.precisa_vistoria_em(reserva.dia_revistoria)
        )
    if not tem_vistoria or reserva.status == 'cancelado':
        abort(404)

    # [FEATURE] coordenador não passa por nenhuma dessas travas — acessa e
    # edita a vistoria/revistoria de qualquer fiscal, mesmo já preenchida.
    if session.get('usuario_perfil') == 'fiscal_fixo':
        # Fiscal fixo só acessa vistorias do seu próprio condomínio.
        usuario_id = session.get('usuario_id')
        cond_do_fiscal = Condominio.query.filter_by(fiscal_id=usuario_id).first()
        if not cond_do_fiscal or cond_do_fiscal.id != condominio.id:
            abort(403)

    elif session.get('usuario_perfil') == 'fiscal':
        # [TESTE] mesma precedência do painel: reivindicação trava pra quem
        # reivindicou. Sem reivindicação, só cai na rota fixa do condomínio
        # se o dia da AÇÃO (vistoria ou revistoria, dependendo do momento)
        # for dia de semana — fim de semana não tem designação fixa, então
        # sem reivindicação fica aberto pra qualquer fiscal de plantão.
        # Defesa em profundidade — sem isso, um link direto/salvo driblaria o
        # filtro da lista.
        usuario_id = session.get('usuario_id')
        if reserva.fiscal_responsavel_id and reserva.fiscal_responsavel_id != usuario_id:
            abort(404)
        if not reserva.fiscal_responsavel_id and condominio.fiscal_id and condominio.fiscal_id != usuario_id:
            # [TESTE] usa a property (respeita data_revistoria_override) em vez
            # de recalcular +1 dia aqui — mesma fonte única de relatorios.py.
            dia_acao = reserva.data_vistoria if momento == 'vistoria' else reserva.dia_revistoria
            eh_fim_de_semana = dia_acao.weekday() >= 5 if dia_acao else False
            if not eh_fim_de_semana:
                abort(404)

    if momento == 'revistoria' and condominio.precisa_vistoria_em(reserva.data_vistoria):
        # Só exige vistoria prévia concluída quando havia uma vistoria da
        # administradora prevista pra essa reserva. Condomínio com fiscal
        # próprio (seg-sáb) muitas vezes só tem a Administradora indo no domingo pra
        # revistoria — nunca existiu vistoria digital antes, e não tem por
        # quê exigir uma.
        vistoria_feita = VistoriaTermo.query.filter_by(
            reserva_id=reserva_id, momento='vistoria'
        ).first()
        if not vistoria_feita:
            flash('A revistoria só pode ser preenchida depois que a vistoria for concluída.', 'danger')
            return _redirect_pos_vistoria()

    grupos_inventario = [
        (s, sorted([i for i in s.inventario if i.ativo], key=lambda i: i.ordem))
        for s in _saloes_alvo(reserva)
    ]
    itens_inventario = [item for _, itens in grupos_inventario for item in itens]
    termo_existente = VistoriaTermo.query.filter_by(
        reserva_id=reserva_id, momento=momento
    ).first()
    regras = _regras_salao(condominio)
    preenchido_em_br = _fmt_br(termo_existente.preenchido_em) if termo_existente else None

    # [TESTE] problema persistente por item — batch numa query só (evita N+1
    # igual ao bug que já corrigi em gerar_pdf_termos). Chave por descrição
    # porque item_inventario_id não é estável (ver comentário em ItemProblema).
    salao_ids = list({s.id for s, _ in grupos_inventario})
    problemas_map = {}
    if salao_ids:
        for p in ItemProblema.query.filter(ItemProblema.salao_id.in_(salao_ids)).all():
            problemas_map[(p.salao_id, p.descricao_item)] = p

    def _render(**extra):
        return render_template('vistoria_form.html',
            reserva=reserva, salao=salao, condominio=condominio,
            momento=momento, grupos_inventario=grupos_inventario, itens_inventario=itens_inventario,
            termo_existente=termo_existente, regras=regras, preenchido_em_br=preenchido_em_br,
            problemas_map=problemas_map, **extra)

    if request.method == 'POST':
        assinatura_fiscal      = _extrai_assinatura('assinatura_fiscal', request.form)
        assinatura_requerente  = _extrai_assinatura('assinatura_requerente', request.form)

        if not assinatura_fiscal or not assinatura_requerente:
            flash('As duas assinaturas (fiscal e requerente) são obrigatórias.', 'danger')
            return _render()

        nome_assinante = sanitize_text(request.form.get('nome_assinante_requerente', ''), 150)
        observacoes    = sanitize_text(request.form.get('observacoes', ''), 2000)
        usuario_id     = session.get('usuario_id')
        fotos_novas    = _extrai_fotos(request.form)

        # [FEATURE] horário de abertura do termo — capturado automaticamente
        # pelo JS no carregamento da tela (não editável pelo fiscal), tanto
        # pra vistoria quanto revistoria. Mesma regex HH:MM de reservas.py
        # (hora_vistoria) e gep_obs, pra manter consistência.
        hora_realizada_raw = request.form.get('hora_realizada', '').strip()[:5]
        hora_realizada = hora_realizada_raw if re.match(r'^([01]\d|2[0-3]):[0-5]\d$', hora_realizada_raw) else None

        # [FEATURE] pedido do Viny: horário da revistoria é combinado com o
        # morador NA vistoria (antes da festa) — só existe esse campo no
        # formulário quando momento == 'vistoria', e vai pra Reserva (não
        # pro termo), porque precisa estar disponível ANTES da revistoria
        # acontecer (pro painel do fiscal mostrar de antemão).
        hora_revistoria = None
        if momento == 'vistoria':
            hora_revistoria_raw = request.form.get('hora_revistoria', '').strip()[:5]
            hora_revistoria = hora_revistoria_raw if re.match(r'^([01]\d|2[0-3]):[0-5]\d$', hora_revistoria_raw) else None

        try:
            if termo_existente:
                termo = termo_existente
                VistoriaItem.query.filter_by(vistoria_termo_id=termo.id).delete()
            else:
                termo = VistoriaTermo(reserva_id=reserva_id, momento=momento)
                db.session.add(termo)

            termo.usuario_id                = usuario_id
            termo.assinatura_fiscal_b64     = assinatura_fiscal
            termo.assinatura_requerente_b64 = assinatura_requerente
            termo.nome_assinante_requerente = nome_assinante
            termo.observacoes               = observacoes
            termo.hora_realizada            = hora_realizada
            termo.preenchido_em             = datetime.utcnow()
            if momento == 'vistoria':
                reserva.hora_revistoria = hora_revistoria
            db.session.flush()

            # [FIX] fotos duplicavam a cada reenvio: no re-submit os
            # VistoriaItem eram apagados e recriados, mas as VistoriaFoto só
            # eram acrescentadas por cima. Isso duplicava quando o fiscal
            # reabria o termo, no duplo submit, e principalmente quando o
            # request chegava no servidor mas a resposta se perdia no 4G (a
            # fila offline reenvia os mesmos bytes). Agora: se o envio traz
            # fotos, elas são a fonte da verdade — apaga as antigas e regrava.
            # Se não traz nenhuma (ex: fiscal só editou observação), mantém as
            # que já estavam.
            if fotos_novas:
                if termo_existente:
                    VistoriaFoto.query.filter_by(vistoria_termo_id=termo.id).delete()
                for foto_bytes in fotos_novas:
                    db.session.add(VistoriaFoto(vistoria_termo_id=termo.id, foto_data=foto_bytes))

            if momento == 'vistoria':
                for item in itens_inventario:
                    presente_item   = f'item_{item.id}' in request.form
                    obs_item        = sanitize_text(request.form.get(f'obs_item_{item.id}', ''), 300)
                    verificado_item = f'verificado_item_{item.id}' in request.form
                    db.session.add(VistoriaItem(
                        vistoria_termo_id=termo.id,
                        item_inventario_id=item.id,
                        presente=presente_item,
                        observacao=obs_item,
                    ))

                    # [TESTE] problema persistente: se veio obs, grava/atualiza;
                    # se o campo veio vazio de propósito (fiscal apagou o texto
                    # que já estava pré-preenchido), interpreta como resolvido.
                    # [FIX] problemas_map só era montado uma vez ANTES do loop,
                    # a partir do banco. Se o inventário do salão tem duas
                    # linhas com a mesma descrição (cadastro duplicado — ver
                    # ex: salão 106 "10 PRATOS DECORATIVOS" repetido), a 1ª
                    # passada cria o ItemProblema mas o map não sabia disso, aí
                    # a 2ª passada tentava criar de novo o mesmo (salao_id,
                    # descricao_item) → UniqueViolation em uq_item_problema_
                    # salao_desc. Agora o map é atualizado a cada iteração, e a
                    # 2ª ocorrência da mesma descrição vira UPDATE, não INSERT.
                    key     = (item.salao_id, item.descricao)
                    problema = problemas_map.get(key)
                    if obs_item:
                        if problema:
                            problema.observacao = obs_item
                            problema.verificado = verificado_item
                        else:
                            problema = ItemProblema(
                                salao_id=item.salao_id, descricao_item=item.descricao,
                                observacao=obs_item, verificado=verificado_item,
                            )
                            db.session.add(problema)
                            problemas_map[key] = problema
                    elif problema:
                        db.session.delete(problema)
                        del problemas_map[key]

            db.session.commit()
            log_audit(
                usuario_id, 'vistoria_preenchida', 'vistoria_termos', termo.id,
                dados_depois={'reserva_id': reserva_id, 'momento': momento, 'fotos': len(fotos_novas)}
            )
        except Exception:
            db.session.rollback()
            logging.exception('Falha ao salvar vistoria digital')
            flash('Erro ao salvar a vistoria. Tente novamente.', 'error')
            return _render()

        flash(f'{"Vistoria" if momento == "vistoria" else "Revistoria"} registrada com sucesso.', 'success')
        return _redirect_pos_vistoria()

    return _render()


@vistorias_bp.route('/vistoria-foto/<int:foto_id>')
@login_required
def ver_foto(foto_id):
    # [TESTE] fotos de problema — imagem individual, uso interno (admin/operador
    # revisando a vistoria). Content-Type fixo porque o cliente sempre exporta
    # JPEG no resize (ver vistoria_form.html).
    foto    = VistoriaFoto.query.get_or_404(foto_id)
    reserva = foto.vistoria_termo.reserva
    _garantir_posse_fiscal(reserva.salao.condominio_id, reserva)

    dados = foto.foto_data
    # [AJUSTE] ?thumb=1 — galeria de conferência do fiscal (celular, 3G/4G):
    # baixar 6 fotos em tamanho cheio só pra confirmar que subiram é caro.
    # Serve uma versão ~600px. Se o Pillow falhar por qualquer motivo, cai
    # no original — nunca quebra a visualização.
    if request.args.get('thumb'):
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(dados))
            im.thumbnail((600, 600))
            buf = io.BytesIO()
            im.convert('RGB').save(buf, format='JPEG', quality=70)
            dados = buf.getvalue()
        except Exception:
            logging.exception('Falha ao gerar thumb da foto %s', foto_id)

    resp = send_file(io.BytesIO(dados), mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@vistorias_bp.route('/coordenacao/fiscais-condominio', methods=['GET', 'POST'])
@login_required
def fiscais_por_condominio():
    # [FEATURE] pedido do Viny: coordenador de fiscais define quem é o
    # fiscal fixo (dia de semana) de cada condomínio, sem precisar de admin
    # mexendo no formulário completo de condomínio (que tem um monte de
    # campo que não tem nada a ver com isso). Tela mobile simples, mesmo
    # estilo do painel do fiscal.
    if session.get('usuario_perfil') not in ('coordenador', 'admin'):
        abort(403)

    if request.method == 'POST':
        try:
            for condominio in Condominio.query.all():
                raw = request.form.get(f'fiscal_{condominio.id}', '')
                condominio.fiscal_id = int(raw) if raw.isdigit() else None
            db.session.commit()
            flash('Fiscais atualizados.', 'success')
        except Exception:
            db.session.rollback()
            logging.exception('Falha ao salvar fiscais por condomínio')
            flash('Erro ao salvar. Tente novamente.', 'error')
        return redirect(url_for('vistorias.fiscais_por_condominio'))

    condominios = Condominio.query.order_by(Condominio.nome).all()
    fiscais     = Usuario.query.filter_by(perfil='fiscal', ativo=True).order_by(Usuario.nome).all()
    return render_template('coordenador_fiscais_condominio.html',
        condominios=condominios, fiscais=fiscais)
