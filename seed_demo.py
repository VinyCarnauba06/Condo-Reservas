"""
seed_demo.py — popula o banco com um dataset de demonstração realista.

Idempotente / aditivo: cada entidade só é criada se ainda não existir
(checagem por login / nome / data-salão). Rode quantas vezes quiser — nada
é apagado e nada duplica.

Cobre:
  - Usuários de todos os perfis (admin, operador, coordenador, fiscal,
    fiscal_fixo) + um inativo.
  - 4 condomínios com salões (dia_inteiro e horário fixo, grupo/combo,
    fiscal próprio seg-sáb, condomínio sem termo) + inventário por salão.
  - ~40 reservas espalhadas de -3 a +2 meses: confirmadas, pendentes,
    canceladas, festa do condomínio e um par de vistoria combinada
    (mesma unidade, dias seguidos).
  - Vistorias digitais (termo de vistoria + revistoria com itens) para
    reservas passadas que exigiam vistoria da administradora.
  - Créditos (usados e disponíveis).
  - Bloqueios de data (globais e por condomínio).
  - Regras de precificação + cota anual por unidade.
  - Feriados (delega ao seed_feriados.py já existente).

Uso:
    python seed_demo.py

Senha de todos os usuários criados: valor de SEED_PASSWORD (env) ou "senha123!".
"""
import os
import sys
import base64
import calendar
import subprocess
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv(override=True)

from app import create_app, db
from app.models import (
    Bloqueio,
    Condominio,
    Credito,
    Historico,
    ItemInventario,
    RegraPrecificacao,
    Reserva,
    ReservaAnualUnidade,
    Salao,
    Usuario,
    VistoriaItem,
    VistoriaTermo,
)

app = create_app()

SENHA_PADRAO = os.getenv("SEED_PASSWORD", "senha123!")
HOJE = date.today()

# PNG 1x1 transparente — placeholder de assinatura digitalizada
_ASSINATURA_B64 = "data:image/png;base64," + (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_stats: dict[str, int] = {}


def _bump(chave: str, n: int = 1) -> None:
    _stats[chave] = _stats.get(chave, 0) + n


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def garantir_schema() -> None:
    """Aplica as migrations (Postgres/prod) ou cai em create_all (SQLite/dev).

    Migrations do projeto usam SQL Postgres-only (CREATE INDEX CONCURRENTLY,
    UPDATE ... FROM). Em SQLite o upgrade a partir do zero quebra — o fallback
    reproduz a convenção do bootstrap_db.py.
    """
    with app.app_context():
        dialeto = db.engine.dialect.name
        try:
            from flask_migrate import upgrade as _upgrade

            _upgrade()
            print(f"[schema] flask db upgrade OK (dialeto={dialeto})")
        except Exception as exc:  # noqa: BLE001 — queremos o fallback amplo aqui
            print(
                f"[schema] upgrade indisponível ({exc.__class__.__name__}: {exc}); "
                "usando db.create_all()"
            )
            db.create_all()
            print(f"[schema] db.create_all() OK (dialeto={dialeto})")


# --------------------------------------------------------------------------- #
# Usuários
# --------------------------------------------------------------------------- #
USUARIOS = [
    # login,          nome,                   perfil,        ativo
    ("marina",        "Marina Souza",         "operador",    True),
    ("rafael",        "Rafael Lima",          "operador",    True),
    ("patricia",      "Patrícia Nunes",       "coordenador", True),
    ("joao.fiscal",   "João Pereira",         "fiscal",      True),
    ("antonio.fixo",  "Antônio Ferreira",     "fiscal_fixo", True),
    ("carlos.antigo", "Carlos Antigo",        "operador",    False),
]


def get_usuario(login, nome, perfil, ativo=True) -> Usuario:
    u = Usuario.query.filter_by(login=login).first()
    if u:
        return u
    u = Usuario(nome=nome, login=login, perfil=perfil, ativo=ativo)
    u.set_senha(SENHA_PADRAO)
    db.session.add(u)
    db.session.flush()
    _bump("usuarios")
    return u


def seed_usuarios() -> dict[str, Usuario]:
    mapa = {}
    for login, nome, perfil, ativo in USUARIOS:
        mapa[login] = get_usuario(login, nome, perfil, ativo)
    db.session.commit()
    return mapa


# --------------------------------------------------------------------------- #
# Condomínios / salões / inventário
# --------------------------------------------------------------------------- #
INVENTARIO_PADRAO = [
    "Mesas plásticas (10)",
    "Cadeiras plásticas (40)",
    "Geladeira / freezer",
    "Forno micro-ondas",
    "Jogo de luzes de festa",
    "Ventiladores de parede (4)",
    "Kit de limpeza",
    "Lixeiras grandes (3)",
]

CONDOMINIOS = [
    {
        "nome": "Residencial Jardim das Acácias",
        "endereco": "Rua das Acácias, 120 - Centro",
        "flags": {"emite_termo": True, "emite_comunicado": True},
        "fiscal_fixo_login": "antonio.fixo",
        "saloes": [
            {
                "nome": "Salão de Festas Principal",
                "valor": 350,
                "forma_pagamento": "Boleto Antecipado",
                "tipo": "dia_inteiro",
                "zelador": "opcional",
            },
            {
                "nome": "Espaço Gourmet",
                "valor": 120,
                "forma_pagamento": "PIX",
                "tipo": "horario_fixo",
                "zelador": "nunca",
                "horarios": "08:00-12:00,13:00-17:00,18:00-22:00",
            },
        ],
    },
    {
        "nome": "Edifício Monte Belo",
        "endereco": "Av. Monte Belo, 900",
        "flags": {
            "emite_termo": True,
            "emite_comunicado": True,
            "exige_adimplencia": True,
        },
        "saloes": [
            {
                "nome": "Salão Térreo",
                "valor": 300,
                "forma_pagamento": "Boleto Antecipado",
                "tipo": "dia_inteiro",
                "zelador": "fds",
                "grupo": "Salões Sociais",
                "combo": False,
            },
            {
                "nome": "Salão Cobertura",
                "valor": 500,
                "forma_pagamento": "Boleto Antecipado",
                "tipo": "dia_inteiro",
                "zelador": "sempre",
                "grupo": "Salões Sociais",
                "combo": True,
            },
        ],
    },
    {
        "nome": "Condomínio Parque das Águas",
        "endereco": "Estrada do Parque, km 3",
        "flags": {
            "emite_termo": True,
            "emite_comunicado": True,
            "fiscal_proprio_seg_sab": True,
            "termo_na_portaria": True,
            "valor_zelador": 90,
        },
        "fiscal_fixo_login": "antonio.fixo",
        "saloes": [
            {
                "nome": "Churrasqueira 1",
                "valor": 150,
                "forma_pagamento": "PIX",
                "tipo": "dia_inteiro",
                "zelador": "opcional",
            },
            {
                "nome": "Churrasqueira 2",
                "valor": 150,
                "forma_pagamento": "PIX",
                "tipo": "dia_inteiro",
                "zelador": "opcional",
            },
        ],
    },
    {
        "nome": "Residencial Vila Nova",
        "endereco": "Rua Nova, 45",
        "flags": {"emite_termo": False, "emite_comunicado": True},
        "saloes": [
            {
                "nome": "Salão Multiuso",
                "valor": 200,
                "forma_pagamento": "Boleto Antecipado",
                "tipo": "dia_inteiro",
                "zelador": "nunca",
            },
            {
                "nome": "Quadra Coberta",
                "valor": 0,
                "forma_pagamento": "Gratuito",
                "tipo": "horario_fixo",
                "zelador": "nunca",
                "horarios": "07:00-09:00,09:00-11:00,19:00-21:00",
            },
        ],
    },
]


def get_condominio(spec, usuarios) -> Condominio:
    cond = Condominio.query.filter_by(nome=spec["nome"]).first()
    if not cond:
        cond = Condominio(nome=spec["nome"], endereco=spec["endereco"], **spec["flags"])
        db.session.add(cond)
        db.session.flush()
        _bump("condominios")
    login_fixo = spec.get("fiscal_fixo_login")
    if login_fixo and cond.fiscal_id is None:
        cond.fiscal_id = usuarios[login_fixo].id

    for s in spec["saloes"]:
        salao = Salao.query.filter_by(condominio_id=cond.id, nome=s["nome"]).first()
        if not salao:
            salao = Salao(
                condominio_id=cond.id,
                nome=s["nome"],
                valor=s["valor"],
                forma_pagamento=s["forma_pagamento"],
                tipo=s["tipo"],
                horarios=s.get("horarios", ""),
                zelador=s.get("zelador", "nunca"),
                grupo=s.get("grupo"),
                combo=s.get("combo", False),
            )
            db.session.add(salao)
            db.session.flush()
            _bump("saloes")

        existentes = {i.descricao for i in salao.inventario}
        for ordem, descricao in enumerate(INVENTARIO_PADRAO):
            if descricao not in existentes:
                db.session.add(
                    ItemInventario(salao_id=salao.id, descricao=descricao, ordem=ordem)
                )
                _bump("itens_inventario")

    db.session.commit()
    return cond


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #
# Contatos propositalmente inválidos: DDD inexistente (00 / 01) e quantidade
# de dígitos fora do padrão brasileiro (7, 10 ou 12 no lugar de 8 ou 9) —
# não colidem com nenhum número real.
MORADORES = [
    ("Ana Beatriz Ramos", "101", "(00) 9981-244"),
    ("Bruno Carvalho", "202", "(01) 99733-10200"),
    ("Camila Fonseca", "304", "(00) 9 9655-7"),
    ("Diego Martins", "405", "(01) 995103-32211"),
    ("Eduarda Lopes", "506-M", "(00) 9422-9"),
    ("Felipe Andrade", "608", "(01) 9 9377-66555"),
    ("Gabriela Pires", "701", "(00) 928-844"),
    ("Henrique Dias", "803", "(01) 91902-277000"),
]

# (meses_relativos, dia_do_mes, status, tipo)
PLANO_RESERVAS = [
    (-3, 12, "confirmado", "vistoria_feita"),
    (-1, 9, "cancelado", "cancelada"),
    (0, 18, "confirmado", "normal"),
    (1, 8, "pendente", "normal"),
    (2, 14, "confirmado", "festa_cond"),
]

# Moradores fictícios usados só nas reservas da semana atual — mesmo critério
# de contato inválido (DDD 00/01, contagem de dígitos fora do padrão).
MORADORES_SEMANA = [
    ("Larissa Monteiro", "1102", "(00) 9812-33"),
    ("Otávio Bezerra", "204", "(01) 982335-56678"),
    ("Priscila Tavares", "907", "(00) 8344-778"),
    ("Rodrigo Aquino", "310-B", "(01) 9 8455-9"),
    ("Sabrina Vasconcelos", "605", "(00) 98566-112233"),
    ("Thiago Marques", "1405", "(01) 986-7733"),
    ("Vanessa Cordeiro", "702", "(00) 9 8788-55667"),
    ("Wesley Barroso", "203", "(01) 8899-7"),
    ("Yara Siqueira", "804", "(00) 989119-90000"),
    ("Zeca Pontes", "406", "(01) 9801-211-22"),
]

# Reservas dentro da semana atual (segunda a domingo).
# (offset_a_partir_de_segunda, indice_do_salao, status, cancelar)
PLANO_SEMANA = [
    (0, 0, "confirmado", False),
    (0, 4, "pendente", False),
    (1, 2, "confirmado", False),
    (2, 1, "confirmado", False),
    (2, 6, "pendente", False),
    (3, 3, "confirmado", False),
    (4, 5, "confirmado", False),
    (4, 0, "confirmado", True),
    (5, 7, "confirmado", False),
    (6, 4, "confirmado", False),
]


def data_relativa(meses: int, dia: int) -> date:
    total = HOJE.month - 1 + meses
    ano = HOJE.year + total // 12
    mes = total % 12 + 1
    dia = min(dia, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def _zelador_seed(salao: Salao, data_festa: date) -> bool:
    if salao.zelador == "sempre":
        return True
    if salao.zelador == "fds":
        return data_festa.weekday() >= 5
    if salao.zelador == "opcional":
        return data_festa.day % 2 == 0
    return False


def _primeiro_horario(salao: Salao) -> str | None:
    if salao.tipo != "horario_fixo":
        return None
    faixas = [h.strip() for h in (salao.horarios or "").split(",") if h.strip()]
    return faixas[0] if faixas else None


def criar_reserva(
    salao: Salao,
    data_festa: date,
    status: str,
    *,
    morador=None,
    horario=None,
    festa_cond=False,
    registrado_por="marina",
    criado_em=None,
    cancelar=False,
    cancel_por="patricia",
    cancel_obs=None,
    observacoes=None,
) -> Reserva | None:
    cond = salao.condominio

    consulta = Reserva.query.filter_by(salao_id=salao.id, data_festa=data_festa)
    if horario:
        consulta = consulta.filter_by(horario=horario)
    # Reservas canceladas do seed também contam para a idempotência (senão
    # seriam recriadas a cada execução, já que ficam fora do filtro de ativas).
    if cancelar:
        existente = consulta.filter_by(status="cancelado").first()
    else:
        existente = consulta.filter(Reserva.status != "cancelado").first()
    if existente:
        return existente

    precisa_vist = (not festa_cond) and cond.precisa_vistoria_em(data_festa)
    criado_em = criado_em or datetime.combine(data_festa, datetime.min.time()) - timedelta(days=25)

    reserva = Reserva(
        salao_id=salao.id,
        data_festa=data_festa,
        data_vistoria=data_festa if precisa_vist else None,
        hora_vistoria="09:00" if precisa_vist else None,
        horario=horario or "",
        status="confirmado" if cancelar else status,
        vistoria=precisa_vist,
        zelador=_zelador_seed(salao, data_festa),
        surpresa=False,
        festa_condominio=festa_cond,
        bloqueia_dia_todo=(salao.tipo == "dia_inteiro"),
        registrado_por=registrado_por,
        observacoes=observacoes,
        criado_em=criado_em,
    )
    if not festa_cond and morador:
        reserva.nome_solicitante = morador[0]
        reserva.apartamento = morador[1]
        reserva.contato = morador[2]

    db.session.add(reserva)
    db.session.flush()
    _bump("reservas")

    db.session.add(
        Historico(
            reserva_id=reserva.id,
            usuario=registrado_por,
            descricao="Reserva criada com status "
            + reserva.status
            + (f" (vistoria em {reserva.data_vistoria:%d/%m/%Y})" if precisa_vist else ""),
            criado_em=criado_em,
        )
    )

    if festa_cond:
        _bump("reservas_festa_condominio")

    if cancelar:
        cancelado_em = datetime.utcnow() - timedelta(days=5)
        reserva.status = "cancelado"
        reserva.cancelado_em = cancelado_em
        reserva.cancelado_por = cancel_por
        reserva.observacao_cancelamento = cancel_obs or "Solicitante desistiu da data."
        db.session.add(
            Historico(
                reserva_id=reserva.id,
                usuario=cancel_por,
                descricao=f"Reserva cancelada — Obs: {reserva.observacao_cancelamento}",
                criado_em=cancelado_em,
            )
        )
        _bump("reservas_canceladas")

    db.session.flush()
    return reserva


def criar_vistoria_digital(reserva: Reserva, fiscal: Usuario) -> None:
    """Termo de vistoria (com itens) + revistoria para uma reserva passada."""
    if not reserva.vistoria:
        return
    itens = [i for i in reserva.salao.inventario if i.ativo]

    for momento in ("vistoria", "revistoria"):
        if VistoriaTermo.query.filter_by(reserva_id=reserva.id, momento=momento).first():
            continue
        termo = VistoriaTermo(
            reserva_id=reserva.id,
            momento=momento,
            usuario_id=fiscal.id,
            assinatura_fiscal_b64=_ASSINATURA_B64,
            assinatura_requerente_b64=_ASSINATURA_B64,
            nome_assinante_requerente=reserva.nome_solicitante or "Responsável pela unidade",
            observacoes=(
                "Itens conferidos, salão em ordem para a festa."
                if momento == "vistoria"
                else "Salão devolvido limpo. Sem avarias relevantes."
            ),
            hora_realizada="09:15" if momento == "vistoria" else "10:30",
            preenchido_em=datetime.combine(reserva.data_festa, datetime.min.time())
            + timedelta(days=0 if momento == "vistoria" else 1, hours=9),
        )
        db.session.add(termo)
        db.session.flush()
        _bump("vistoria_termos")

        # Itens só são registrados na vistoria (antes), igual à rota real.
        if momento == "vistoria":
            for idx, item in enumerate(itens):
                avaria = idx == 1  # "cadeiras" com ressalva, só pra ter um caso
                db.session.add(
                    VistoriaItem(
                        vistoria_termo_id=termo.id,
                        item_inventario_id=item.id,
                        presente=True,
                        observacao="1 unidade riscada, sem comprometer o uso" if avaria else None,
                    )
                )
                _bump("vistoria_itens")


def seed_reservas(condominios: list[Condominio], usuarios: dict[str, Usuario]) -> None:
    fiscal = usuarios["joao.fiscal"]
    registradores = ["marina", "rafael"]

    saloes = [s for c in condominios for s in c.saloes]
    for s_idx, salao in enumerate(saloes):
        for p_idx, (meses, dia, status, tipo) in enumerate(PLANO_RESERVAS):
            data_festa = data_relativa(meses, dia + s_idx % 5)
            morador = MORADORES[(s_idx + p_idx) % len(MORADORES)]
            horario = _primeiro_horario(salao)
            festa_cond = tipo == "festa_cond"
            cancelar = tipo == "cancelada"

            reserva = criar_reserva(
                salao,
                data_festa,
                status,
                morador=None if festa_cond else morador,
                horario=horario,
                festa_cond=festa_cond,
                registrado_por=registradores[(s_idx + p_idx) % len(registradores)],
                cancelar=cancelar,
                cancel_obs="Mudança de data pela família." if cancelar else None,
                observacoes="Aniversário infantil." if tipo == "normal" else None,
            )

            if reserva and tipo == "vistoria_feita":
                criar_vistoria_digital(reserva, fiscal)

    _seed_vistoria_combinada(condominios[0], usuarios)
    db.session.commit()


def seed_reservas_semana(condominios: list[Condominio], usuarios: dict[str, Usuario]) -> None:
    """Reservas espalhadas pela semana atual (segunda a domingo), com
    solicitantes, contatos e apartamentos fictícios."""
    saloes = [s for c in condominios for s in c.saloes]
    segunda = HOJE - timedelta(days=HOJE.weekday())
    registradores = ["marina", "rafael"]

    for i, (offset, s_idx, status, cancelar) in enumerate(PLANO_SEMANA):
        salao = saloes[s_idx % len(saloes)]
        data_festa = segunda + timedelta(days=offset)
        morador = MORADORES_SEMANA[i % len(MORADORES_SEMANA)]

        criar_reserva(
            salao,
            data_festa,
            status,
            morador=morador,
            horario=_primeiro_horario(salao),
            registrado_por=registradores[i % len(registradores)],
            criado_em=datetime.utcnow() - timedelta(days=3),
            cancelar=cancelar,
            cancel_obs="Solicitante remarcou para outra data." if cancelar else None,
            observacoes="Reserva da semana.",
        )

    db.session.commit()


def _seed_vistoria_combinada(cond: Condominio, usuarios: dict[str, Usuario]) -> None:
    """Par de reservas: mesma unidade, mesmo salão, dias seguidos.

    Reproduz o efeito de reservas.nova_reserva(): o 2º dia não tem vistoria
    própria e a revistoria da 1ª reserva é remarcada para D+1 do último dia.
    """
    salao = next((s for s in cond.saloes if s.tipo == "dia_inteiro"), None)
    if salao is None:
        return

    dia1 = data_relativa(1, 24)
    dia2 = dia1 + timedelta(days=1)
    morador = ("Isadora Nogueira", "902", "(00) 9910-18-080")

    if Reserva.query.filter_by(salao_id=salao.id, data_festa=dia1).filter(
        Reserva.status != "cancelado"
    ).first():
        return

    r1 = criar_reserva(salao, dia1, "confirmado", morador=morador, registrado_por="marina")
    r2 = criar_reserva(salao, dia2, "confirmado", morador=morador, registrado_por="marina")
    if not r1 or not r2:
        return

    r2.vistoria = False
    r2.data_vistoria = None
    r2.hora_vistoria = None
    r2.vistoria_pareada_com_id = r1.id
    if r1.vistoria:
        r1.data_revistoria_override = dia2 + timedelta(days=1)

    db.session.add(
        Historico(
            reserva_id=r2.id,
            usuario="marina",
            descricao=(
                f"Vistoria combinada com a reserva de {dia1:%d/%m/%Y} "
                "(mesma unidade, dias seguidos) — sem vistoria própria"
            ),
            criado_em=r2.criado_em,
        )
    )
    db.session.add(
        Historico(
            reserva_id=r1.id,
            usuario="marina",
            descricao=f"Revistoria remarcada para {dia2 + timedelta(days=1):%d/%m/%Y} — festa combinada",
            criado_em=r1.criado_em,
        )
    )
    _bump("reservas_vistoria_combinada", 2)


# --------------------------------------------------------------------------- #
# Créditos
# --------------------------------------------------------------------------- #
def seed_creditos(condominios: list[Condominio]) -> None:
    cond = condominios[0]
    salao = cond.saloes[0]

    # Crédito disponível (sobra de pagamento).
    if not Credito.query.filter_by(
        condominio_id=cond.id, salao_id=salao.id, apartamento="101", usado=False
    ).first():
        db.session.add(
            Credito(
                condominio_id=cond.id,
                salao_id=salao.id,
                apartamento="101",
                valor=350,
                observacao="Pagamento em duplicidade — usar na próxima reserva",
                usado=False,
            )
        )
        _bump("creditos")

    # Crédito já consumido, amarrado a uma reserva confirmada real do salão.
    alvo = (
        Reserva.query.filter_by(salao_id=salao.id, status="confirmado")
        .filter(Reserva.festa_condominio.is_(False))
        .order_by(Reserva.data_festa.desc())
        .first()
    )
    if alvo and alvo.apartamento and not Credito.query.filter_by(reserva_id=alvo.id).first():
        db.session.add(
            Credito(
                condominio_id=cond.id,
                salao_id=salao.id,
                reserva_id=alvo.id,
                apartamento=alvo.apartamento.strip().upper(),
                valor=120,
                observacao="Crédito de cortesia aplicado na reserva",
                usado=True,
            )
        )
        _bump("creditos")

    db.session.commit()


# --------------------------------------------------------------------------- #
# Bloqueios
# --------------------------------------------------------------------------- #
def seed_bloqueios(condominios: list[Condominio]) -> None:
    plano = [
        (None, data_relativa(0, 5), data_relativa(0, 6), "Dedetização geral — todos os condomínios"),
        (condominios[1].id, data_relativa(1, 1), data_relativa(1, 10), "Reforma da cobertura"),
        (condominios[0].id, data_relativa(-1, 24), data_relativa(-1, 26), "Manutenção elétrica do salão"),
    ]
    for condominio_id, inicio, fim, descricao in plano:
        existe = Bloqueio.query.filter_by(
            condominio_id=condominio_id, data_inicio=inicio, descricao=descricao
        ).first()
        if not existe:
            db.session.add(
                Bloqueio(
                    condominio_id=condominio_id,
                    data_inicio=inicio,
                    data_fim=fim,
                    descricao=descricao,
                )
            )
            _bump("bloqueios")
    db.session.commit()


# --------------------------------------------------------------------------- #
# Regras de precificação + cota anual
# --------------------------------------------------------------------------- #
def seed_regras_precificacao(condominios: list[Condominio]) -> None:
    cond_aguas = condominios[2]
    cond_vila = condominios[3]
    alvos = [
        (cond_aguas.saloes[0], 1, 80, "1ª reserva do ano isenta por unidade"),
        (cond_vila.saloes[0], 2, 100, "Até 2 reservas isentas por unidade/ano"),
    ]

    for salao, limite, valor_opcional, descricao in alvos:
        regra = RegraPrecificacao.query.filter_by(salao_id=salao.id).first()
        if not regra:
            regra = RegraPrecificacao(
                salao_id=salao.id,
                limite_reservas=limite,
                valor_opcional=valor_opcional,
                descricao=descricao,
                ativo=True,
            )
            db.session.add(regra)
            _bump("regras_precificacao")
        db.session.flush()

        # Marca a reserva confirmada mais recente do salão como isenta (cota).
        reserva = (
            Reserva.query.filter_by(salao_id=salao.id, status="confirmado")
            .filter(Reserva.festa_condominio.is_(False))
            .order_by(Reserva.data_festa.desc())
            .first()
        )
        if reserva and reserva.apartamento and not ReservaAnualUnidade.query.filter_by(
            reserva_id=reserva.id
        ).first():
            db.session.add(
                ReservaAnualUnidade(
                    condominio_id=salao.condominio_id,
                    salao_id=salao.id,
                    apartamento=reserva.apartamento.strip().upper(),
                    ano=reserva.data_festa.year,
                    reserva_id=reserva.id,
                    valor_opcional_usado=False,
                )
            )
            db.session.add(
                Historico(
                    reserva_id=reserva.id,
                    usuario="marina",
                    descricao=f"Reserva isenta — {descricao} (1ª/{limite} do ano {reserva.data_festa.year})",
                    criado_em=reserva.criado_em,
                )
            )
            _bump("cota_anual")

    db.session.commit()


# --------------------------------------------------------------------------- #
# Feriados
# --------------------------------------------------------------------------- #
def seed_feriados() -> None:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_feriados.py")
    if not os.path.exists(script):
        print("[feriados] seed_feriados.py não encontrado — pulando")
        return
    try:
        out = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        msg = (out.stdout or out.stderr or "").strip().splitlines()
        print(f"[feriados] {msg[-1] if msg else 'executado'}")
    except Exception as exc:  # noqa: BLE001
        print(f"[feriados] falha ao executar seed_feriados.py: {exc}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    garantir_schema()
    seed_feriados()

    with app.app_context():
        usuarios = seed_usuarios()
        condominios = [get_condominio(spec, usuarios) for spec in CONDOMINIOS]
        seed_reservas(condominios, usuarios)
        seed_reservas_semana(condominios, usuarios)
        seed_creditos(condominios)
        seed_bloqueios(condominios)
        seed_regras_precificacao(condominios)

    print("\n=== Seed concluído ===")
    if _stats:
        for chave in sorted(_stats):
            print(f"  {chave:28s} +{_stats[chave]}")
    else:
        print("  Nada novo — banco já estava populado.")
    print(f"\nUsuários de teste (senha: {SENHA_PADRAO}):")
    print("  admin / operador: marina, rafael / coordenador: patricia")
    print("  fiscal: joao.fiscal / fiscal_fixo: antonio.fixo / inativo: carlos.antigo")


if __name__ == "__main__":
    main()
