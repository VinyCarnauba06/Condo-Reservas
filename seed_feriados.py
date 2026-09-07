from app import create_app, db
from app.models import Feriado

app = create_app()

FERIADOS = [
    # =========================
    # FERIADOS NACIONAIS FIXOS
    # =========================
    ('Confraternização Universal',   1,  1, None),
    ('Tiradentes',                  21,  4, None),
    ('Dia do Trabalho',              1,  5, None),
    ('Independência do Brasil',      7,  9, None),
    ('Nossa Senhora Aparecida',     12, 10, None),
    ('Finados',                      2, 11, None),
    ('Proclamação da República',    15, 11, None),
    ('Natal',                       25, 12, None),

    # =========================
    # FERIADOS ESTADUAIS - AL
    # =========================
    ('São João',                    24,  6, None),
    ('Emancipação Política de Alagoas', 16, 9, None),
    ('Zumbi dos Palmares',          20, 11, None),
    ('Dia Estadual do Evangélico',  30, 11, None),

    # =========================
    # FERIADOS MUNICIPAIS (exemplo — ajuste para sua cidade)
    # =========================
    ('Marechal Floriano Peixoto',   29,  6, None),
    ('Nossa Senhora dos Prazeres',  27,  8, None),
    ('Nossa Senhora da Conceição',   8, 12, None),

    # =========================
    # FERIADOS VARIÁVEIS - 2026
    # =========================
    ('Carnaval',                    16,  2, 2026),
    ('Carnaval',                    17,  2, 2026),
    ('Quarta-feira de Cinzas',      18,  2, 2026),
    ('Quinta-feira Santa',           2,  4, 2026),
    ('Sexta-feira Santa',            3,  4, 2026),
    ('Corpus Christi',               4,  6, 2026),

    # =========================
    # FERIADOS VARIÁVEIS - 2027
    # =========================
    ('Carnaval',                     8,  2, 2027),
    ('Carnaval',                     9,  2, 2027),
    ('Quarta-feira de Cinzas',      10,  2, 2027),
    ('Quinta-feira Santa',          25,  3, 2027),
    ('Sexta-feira Santa',           26,  3, 2027),
    ('Corpus Christi',              27,  5, 2027),
]

with app.app_context():
    inseridos = 0

    for nome, dia, mes, ano in FERIADOS:
        existe = Feriado.query.filter_by(
            nome=nome,
            dia=dia,
            mes=mes,
            ano=ano
        ).first()

        if not existe:
            db.session.add(
                Feriado(
                    nome=nome,
                    dia=dia,
                    mes=mes,
                    ano=ano
                )
            )
            inseridos += 1

    db.session.commit()

    print(f'{inseridos} feriados inseridos com sucesso.')