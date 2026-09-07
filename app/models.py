import os
import logging
from app import db
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.ext.hybrid import hybrid_property
from cryptography.fernet import Fernet, InvalidToken
from bleach import clean as _bleach_clean


def sanitize_text(texto, max_length=500):
    if not texto:
        return None
    texto = _bleach_clean(str(texto), tags=[], attributes={}, strip=True)
    texto = ' '.join(texto.split())
    return texto[:max_length].strip() or None

class Condominio(db.Model):
    __tablename__     = 'condominios'
    id                = db.Column(db.Integer, primary_key=True)
    nome              = db.Column(db.String(200), nullable=False)
    endereco          = db.Column(db.String(300))
    observacoes          = db.Column(db.Text)
    comunicados_salao    = db.Column(db.Text, nullable=True)
    qtd_comunicados      = db.Column(db.Integer, default=3, nullable=False)
    exige_adimplencia    = db.Column(db.Boolean, default=False)
    emite_comunicado  = db.Column(db.Boolean, default=True, nullable=False)
    emite_termo       = db.Column(db.Boolean, default=True, nullable=False)
    # [FEATURE] KAÁ — termo fotográfico: ao invés do checklist puro, o fiscal
    # tira fotos dos itens do salão. Só o KAÁ tem True por enquanto; o design
    # é genérico (qualquer condomínio pode ligar no futuro).
    termo_fotografico = db.Column(db.Boolean, default=False, nullable=False)
    regimento_pdf     = db.Column(db.String(300), nullable=True)
    regimento_pdf_data = db.Column(db.LargeBinary, nullable=True)
    criado_em         = db.Column(db.DateTime, default=datetime.utcnow)
    saloes            = db.relationship('Salao', backref=db.backref('condominio', lazy='joined'), lazy=True, cascade='all, delete-orphan')

    # [TESTE] vistoria mobile — fiscal fixo pra dias de semana (seg-sex). Fim
    # de semana usa reserva.fiscal_responsavel_id (auto-reivindicado), não isso.
    fiscal_id         = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    fiscal            = db.relationship('Usuario', foreign_keys=[fiscal_id])

    # [TESTE] fiscal próprio (seg-sáb) — condomínio tem fiscal fixo dele que
    # cobre a vistoria de segunda a sábado por conta própria, fora do app. Só
    # domingo (dia em que o fiscal próprio não trabalha) que a vistoria/
    # revistoria passa a ser da administradora. Só faz efeito se emite_termo
    # também estiver ativo (emite_termo continua sendo o gate master).
    fiscal_proprio_seg_sab = db.Column(db.Boolean, default=False, nullable=False)

    # [TESTE] termo na portaria — só faz sentido com fiscal_proprio_seg_sab
    # ativo. Hoje: Residencial Costa Azul e Edifício Panorama. O termo de requisição físico já fica
    # na portaria do condomínio, então o "Termo" gerado pela Administradora deixa de
    # ser o documento legal completo (regras/inventário/assinaturas) e vira
    # só um aviso de apoio fiscal pro domingo. Ver
    # relatorios.story_solicitacao_apoio().
    termo_na_portaria = db.Column(db.Boolean, default=False, nullable=False)

    # [TESTE] recibo de zelador automatizado — valor cobrado do condomínio
    # pela limpeza pós-festa (Reserva.zelador=True) e nota livre (ex:
    # "morador paga ao fiscal"). obs_zelador é só informativo por enquanto,
    # não muda a geração do recibo — regra condicional fica pra depois.
    valor_zelador = db.Column(db.Numeric(10, 2), nullable=True)
    obs_zelador   = db.Column(db.String(300), nullable=True)

    # [TESTE] recibo de zelador — "morador paga ao fiscal": recibo endereçado
    # ao condômino pelo nome (não ao condomínio) + linha extra de assinatura
    # condômino/fiscal. Hoje só Edifício Bela Vista e Residencial Palmeiras.
    zelador_pago_direto_fiscal = db.Column(db.Boolean, default=False, nullable=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def precisa_vistoria_em(self, dia):
        """True se, na data `dia`, a vistoria/revistoria dessa reserva é da
        administradora (Administradora) — e não do fiscal próprio do condomínio.
        Fonte única de verdade: reservas.py (grava data_vistoria), vistorias.py
        (painel do fiscal e guard de acesso) e os templates que mostram os
        chips de VISTORIA/REVISTORIA todos chamam este método."""
        if not self.emite_termo or dia is None:
            return False
        if self.fiscal_proprio_seg_sab:
            return dia.weekday() == 6  # domingo — só dia que o fiscal próprio não cobre
        return True


class Salao(db.Model):
    __tablename__   = 'saloes'
    id              = db.Column(db.Integer, primary_key=True)
    condominio_id   = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=False, index=True)
    nome            = db.Column(db.String(200), nullable=False)
    valor           = db.Column(db.Numeric(10, 2), nullable=False)
    forma_pagamento = db.Column(db.String(100), nullable=False)
    tipo            = db.Column(db.String(20), default='dia_inteiro')   
    horarios        = db.Column(db.String(500))
    zelador         = db.Column(db.String(20), default='nunca')
    grupo           = db.Column(db.String(100), nullable=True)
    combo           = db.Column(db.Boolean, default=False)
    reservas        = db.relationship('Reserva', backref=db.backref('salao', lazy='joined'), lazy='selectin', cascade='all, delete-orphan')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Reserva(db.Model):
    __tablename__         = 'reservas'
    id                    = db.Column(db.Integer, primary_key=True)
    salao_id              = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=False)
    _nome_solicitante_enc = db.Column(db.String(500), nullable=True)
    _contato_enc          = db.Column(db.String(500))
    _apartamento_enc      = db.Column(db.String(500), nullable=True)
    data_festa            = db.Column(db.Date, nullable=False)
    data_vistoria         = db.Column(db.Date, nullable=True)
    hora_vistoria         = db.Column(db.String(5), nullable=True)
    # [FEATURE] pedido do Viny: horário da revistoria é combinado com o
    # morador NA HORA da vistoria (antes da festa) — o fiscal preenche isso
    # no formulário de vistoria, embaixo das assinaturas, não no dia da
    # revistoria em si. Igual a hora_vistoria em formato/uso: horário
    # "marcado" pra revistoria, exibido de cara no painel do fiscal.
    hora_revistoria       = db.Column(db.String(5), nullable=True)
    horario               = db.Column(db.String(50))
    status                = db.Column(db.String(20), default='pendente')
    vistoria              = db.Column(db.Boolean, default=False)
    zelador               = db.Column(db.Boolean, default=False)
    surpresa              = db.Column(db.Boolean, default=False)
    festa_condominio      = db.Column(db.Boolean, default=False, nullable=False)
    # [FEATURE] denormalizado a partir de salao.tipo == 'dia_inteiro' no
    # momento da criação. Existe só pra viabilizar o índice único parcial
    # ix_reserva_dia_inteiro_unico (ver migration): índice não pode ter
    # predicado que dependa de outra tabela, então gravamos o flag aqui.
    # NÃO reflete mudança de Salao.tipo feita depois da reserva existir.
    bloqueia_dia_todo     = db.Column(db.Boolean, default=False, nullable=False)
    boleto_gerado         = db.Column(db.Boolean, default=False)
    boleto_enviado        = db.Column(db.Boolean, default=False)
    boleto_pago           = db.Column(db.Boolean, default=False)
    registrado_por        = db.Column(db.String(100))
    observacoes           = db.Column(db.String(500))
    impresso              = db.Column(db.Boolean, default=False, nullable=False)
    # [TESTE] overrides manuais de termo/comunicado — caso raro (ex: festa em
    # dois dias seguidos, síndico pede motivo customizado tipo "Arraiá do
    # condomínio"). Nullable: quando None, PDF usa o cálculo/texto padrão.
    motivo_festa               = db.Column(db.String(200), nullable=True)
    data_revistoria_override   = db.Column(db.Date, nullable=True)
    comunicado_texto_override  = db.Column(db.String(500), nullable=True)
    # [FEATURE] festa em 2+ dias seguidos, mesma unidade, mesmo salão —
    # detectado automaticamente em nova_reserva(). Aponta pra reserva "dona"
    # (1º dia): essa aqui é o "filho" da vistoria combinada, não tem vistoria
    # própria (vistoria=False, data_vistoria=None). A dona recebe
    # data_revistoria_override = filho.data_festa + 1. Sempre None em quem é
    # dono. Repactuado em cancelar_reserva() se qualquer uma das duas for
    # cancelada — ver comentários lá.
    vistoria_pareada_com_id   = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=True)
    vistoria_pareada_com      = db.relationship('Reserva', remote_side=[id])
    criado_em             = db.Column(db.DateTime, default=datetime.utcnow)
    cancelado_em          = db.Column(db.DateTime)
    cancelado_por         = db.Column(db.String(100))
    observacao_cancelamento = db.Column(db.String(500))
    historico             = db.relationship('Historico', backref='reserva', lazy='selectin', cascade='all, delete-orphan')

    # [TESTE] vistoria mobile — fim de semana não tem fiscal fixo por
    # condomínio (só 2/3 de plantão, variável). Cada um reivindica a vistoria
    # que vai cobrir; fica travada só pra ele até liberar. Ignorado em dias
    # de semana (lá a rota é por Condominio.fiscal_id).
    fiscal_responsavel_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    fiscal_responsavel     = db.relationship('Usuario', foreign_keys=[fiscal_responsavel_id])

    @property
    def dia_revistoria(self):
        # [TESTE] fiscal próprio (seg-sáb) — revistoria não é campo persistido
        # (sempre foi calculada como data_festa + 1 em tempo de leitura); esta
        # property centraliza essa conta pra models/rotas/templates usarem o
        # mesmo cálculo com Condominio.precisa_vistoria_em(reserva.dia_revistoria).
        # [TESTE] data_revistoria_override — caso raro de festa em dois dias
        # seguidos, onde D+1 já tem outra festa e a revistoria precisa mudar
        # de dia manualmente. Se setado, prevalece sobre o cálculo padrão.
        if self.data_revistoria_override:
            return self.data_revistoria_override
        return self.data_festa + timedelta(days=1) if self.data_festa else None

    @property
    def exige_vistoria_previa(self):
        # [FIX] consistência painel x rota: o chip "REVISTORIA" só deve
        # aparecer travado ("faça a vistoria antes") quando a rota realmente
        # exige uma vistoria prévia concluída. A rota decide isso com
        # Condominio.precisa_vistoria_em(data_vistoria) (recalculado, respeita
        # emite_termo / fiscal_proprio_seg_sab atuais) — o template usava só
        # `data_vistoria` cru (dado congelado na criação) e divergia.
        cond = self.salao.condominio if self.salao else None
        return bool(cond and cond.precisa_vistoria_em(self.data_vistoria))

    @property
    def cancelado_em_br(self):
        # [FEATURE] cancelado_em é salvo em UTC (datetime.now(timezone.utc)).
        # Exibir/comparar dia da semana direto em UTC desloca a data de
        # cancelamentos tarde da noite (ex: sábado 23h BR = domingo 02h UTC).
        # Fonte única pra quem precisar do dia certo em horário de Brasília.
        if not self.cancelado_em:
            return None
        import pytz
        tz = pytz.timezone('America/Sao_Paulo')
        dt = self.cancelado_em
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt.astimezone(tz)

    __table_args__ = (
        db.Index('ix_reserva_salao_id', 'salao_id'),
        db.Index('ix_reserva_data_festa', 'data_festa'),
        db.Index('ix_reserva_status', 'status'),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    def _get_cipher():
        key = os.getenv('ENCRYPTION_KEY')
        if not key:
            return None
        try:
            return Fernet(key.encode() if isinstance(key, str) else key)
        except Exception:
            return None

    @hybrid_property
    def nome_solicitante(self):
        cipher = self._get_cipher()
        if cipher is None or self._nome_solicitante_enc is None:
            return self._nome_solicitante_enc
        try:
            return cipher.decrypt(self._nome_solicitante_enc.encode()).decode()
        except (InvalidToken, Exception) as e:
            logging.error(f"Erro ao descriptografar nome_solicitante: {e}")
            return None

    @nome_solicitante.setter
    def nome_solicitante(self, value):
        cipher = self._get_cipher()
        if cipher is None or value is None:
            self._nome_solicitante_enc = value
        else:
            self._nome_solicitante_enc = cipher.encrypt(value.encode()).decode()

    @nome_solicitante.expression
    def nome_solicitante(cls):
        return cls._nome_solicitante_enc

    @hybrid_property
    def contato(self):
        cipher = self._get_cipher()
        if cipher is None or self._contato_enc is None:
            return self._contato_enc
        try:
            return cipher.decrypt(self._contato_enc.encode()).decode()
        except (InvalidToken, Exception) as e:
            logging.error(f"Erro ao descriptografar contato: {e}")
            return None

    @contato.setter
    def contato(self, value):
        cipher = self._get_cipher()
        if cipher is None or value is None:
            self._contato_enc = value
        else:
            self._contato_enc = cipher.encrypt(value.encode()).decode()

    @contato.expression
    def contato(cls):
        return cls._contato_enc

    @hybrid_property
    def apartamento(self):
        cipher = self._get_cipher()
        if cipher is None or self._apartamento_enc is None:
            return self._apartamento_enc
        try:
            return cipher.decrypt(self._apartamento_enc.encode()).decode()
        except (InvalidToken, Exception) as e:
            logging.error(f"Erro ao descriptografar apartamento: {e}")
            return None

    @apartamento.setter
    def apartamento(self, value):
        cipher = self._get_cipher()
        if cipher is None or value is None:
            self._apartamento_enc = value
        else:
            self._apartamento_enc = cipher.encrypt(value.encode()).decode()

    @apartamento.expression
    def apartamento(cls):
        return cls._apartamento_enc


class Historico(db.Model):  
    __tablename__ = 'historico'
    id            = db.Column(db.Integer, primary_key=True)
    reserva_id    = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=False)
    descricao     = db.Column(db.Text, nullable=False)
    usuario       = db.Column(db.String(100))
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_historico_reserva_id', 'reserva_id'),
        db.Index('ix_historico_criado_em', 'criado_em'),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Credito(db.Model):
    __tablename__ = 'creditos'
    id            = db.Column(db.Integer, primary_key=True)
    condominio_id = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=False, index=True)
    salao_id      = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=False)
    reserva_id    = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=True)
    apartamento   = db.Column(db.String(20), nullable=False)
    valor         = db.Column(db.Numeric(10, 2), nullable=False)
    observacao    = db.Column(db.String(200), nullable=True)
    usado         = db.Column(db.Boolean, default=False)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    condominio    = db.relationship('Condominio', backref='creditos')
    salao         = db.relationship('Salao',      backref='creditos')
    reserva       = db.relationship('Reserva',    backref='creditos', foreign_keys=[reserva_id])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Bloqueio(db.Model):
    __tablename__ = 'bloqueios'
    id            = db.Column(db.Integer, primary_key=True)
    data_inicio   = db.Column(db.Date, nullable=False)
    data_fim      = db.Column(db.Date, nullable=False)
    descricao     = db.Column(db.String(200), nullable=False)
    condominio_id = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    condominio    = db.relationship('Condominio', backref='bloqueios')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Feriado(db.Model):
    __tablename__ = 'feriados'
    id            = db.Column(db.Integer, primary_key=True)
    nome          = db.Column(db.String(100), nullable=False)
    dia           = db.Column(db.Integer, nullable=False)
    mes           = db.Column(db.Integer, nullable=False)
    ano           = db.Column(db.Integer)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id            = db.Column(db.Integer, primary_key=True)
    nome          = db.Column(db.String(100), nullable=False)
    login = db.Column(db.String(100), unique=True, nullable=False, index=True)
    senha_hash    = db.Column(db.String(255), nullable=False)
    ativo         = db.Column(db.Boolean, default=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    perfil = db.Column(db.String(20), nullable=False, default='operador')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def checar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

class ItemInventario(db.Model):
    __tablename__ = 'itens_inventario'
    id        = db.Column(db.Integer, primary_key=True)
    salao_id  = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=False, index=True)
    descricao = db.Column(db.String(200), nullable=False)
    ordem     = db.Column(db.Integer, default=0)
    # [FIX] admin_inventario() apagava e recriava TODOS os itens do salão a
    # cada "Salvar" — quebra com ForeignKeyViolation assim que qualquer item
    # já foi usado numa vistoria digital (VistoriaItem.item_inventario_id
    # referencia o id antigo). Agora item com histórico vira ativo=False em
    # vez de apagado de verdade; todo lugar que lista inventário "de uso"
    # (form de vistoria, PDF de termo, tela de edição) filtra ativo=True.
    ativo     = db.Column(db.Boolean, default=True, nullable=False)
    salao     = db.relationship('Salao', backref='inventario')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# [TESTE] Vistoria mobile — ver CHANGELOG_VISTORIA_MOBILE.md (raiz do projeto) pra reverter
class VistoriaTermo(db.Model):
    __tablename__              = 'vistoria_termos'
    id                         = db.Column(db.Integer, primary_key=True)
    reserva_id                 = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=False, index=True)
    momento                    = db.Column(db.String(10), nullable=False)  # 'antes' | 'depois'
    usuario_id                 = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    assinatura_fiscal_b64      = db.Column(db.Text, nullable=False)
    assinatura_requerente_b64  = db.Column(db.Text, nullable=False)
    nome_assinante_requerente  = db.Column(db.String(150))
    observacoes                = db.Column(db.Text)
    # [FEATURE] pedido dos fiscais: horário digitado à mão (HH:MM) de quando
    # a vistoria/revistoria foi de fato realizada no local — diferente de
    # preenchido_em, que é quando o formulário foi salvo no servidor (pode
    # ser bem depois, principalmente com o preenchimento offline, que só
    # sincroniza quando a internet volta). Aparece no termo junto com o
    # horário marcado (Reserva.hora_vistoria).
    hora_realizada             = db.Column(db.String(5), nullable=True)
    preenchido_em              = db.Column(db.DateTime, default=datetime.utcnow)
    reserva                    = db.relationship('Reserva', backref='vistorias_termo')
    usuario                    = db.relationship('Usuario')
    itens                      = db.relationship('VistoriaItem', backref='vistoria_termo', lazy='selectin', cascade='all, delete-orphan')
    fotos                      = db.relationship('VistoriaFoto', backref='vistoria_termo', lazy='selectin', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('reserva_id', 'momento', name='uq_vistoria_reserva_momento'),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class VistoriaItem(db.Model):
    __tablename__       = 'vistoria_itens'
    id                  = db.Column(db.Integer, primary_key=True)
    vistoria_termo_id   = db.Column(db.Integer, db.ForeignKey('vistoria_termos.id'), nullable=False, index=True)
    item_inventario_id  = db.Column(db.Integer, db.ForeignKey('itens_inventario.id'), nullable=False)
    presente            = db.Column(db.Boolean, default=True, nullable=False)
    observacao          = db.Column(db.String(300))
    item_inventario     = db.relationship('ItemInventario')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# [TESTE] Vistoria mobile — foto de problema (cadeira quebrada, pintura, etc.),
# vinculada ao termo (não a um item específico — ver CHANGELOG_VISTORIA_MOBILE.md).
class VistoriaFoto(db.Model):
    __tablename__      = 'vistoria_fotos'
    id                 = db.Column(db.Integer, primary_key=True)
    vistoria_termo_id  = db.Column(db.Integer, db.ForeignKey('vistoria_termos.id'), nullable=False, index=True)
    foto_data          = db.Column(db.LargeBinary, nullable=False)
    descricao          = db.Column(db.String(200))
    criado_em          = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


# [TESTE] Vistoria mobile — problema persistente de um item do inventário
# (ex: "só tem 7 cadeiras, 4 quebradas"), fica valendo pros próximos termos
# até alguém marcar como resolvido.
#
# Chaveado por (salao_id, descricao_item) em vez de item_inventario_id de
# propósito: `admin_inventario` (app/routes/relatorios.py) DELETA e RECRIA
# todos os ItemInventario de um salão a cada "Salvar inventário" — qualquer
# FK pro id antigo quebraria/sumiria na próxima edição não relacionada. A
# descrição do item é o identificador estável do ponto de vista de quem usa.
class ItemProblema(db.Model):
    __tablename__   = 'itens_problema'
    id              = db.Column(db.Integer, primary_key=True)
    salao_id        = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=False, index=True)
    descricao_item  = db.Column(db.String(200), nullable=False, index=True)
    observacao      = db.Column(db.Text, nullable=False)
    verificado      = db.Column(db.Boolean, default=False, nullable=False)
    registrado_em   = db.Column(db.DateTime, default=datetime.utcnow)
    salao           = db.relationship('Salao')

    __table_args__ = (
        db.UniqueConstraint('salao_id', 'descricao_item', name='uq_item_problema_salao_desc'),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class RegraRegimento(db.Model):
    __tablename__ = 'regras_regimento'
    id            = db.Column(db.Integer, primary_key=True)
    condominio_id = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=False)
    texto         = db.Column(db.Text, nullable=False)
    condominio    = db.relationship('Condominio', backref='regras')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DocumentoGerado(db.Model):
    __tablename__ = 'documentos_gerados'
    id            = db.Column(db.Integer, primary_key=True)
    tipo          = db.Column(db.String(20), nullable=False)
    reserva_id    = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=True)
    condominio_id = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=True)
    semana_inicio = db.Column(db.Date, nullable=False)
    gerado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    reserva       = db.relationship('Reserva', backref='documentos')
    condominio    = db.relationship('Condominio', backref='documentos')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id          = db.Column(db.Integer, primary_key=True)
    usuario_id  = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    acao        = db.Column(db.String(100), nullable=False, index=True)
    tabela      = db.Column(db.String(50))
    registro_id = db.Column(db.Integer)
    dados_antes  = db.Column(db.JSON)
    dados_depois = db.Column(db.JSON)
    ip_address  = db.Column(db.String(50))
    user_agent  = db.Column(db.String(500))
    criado_em   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    usuario     = db.relationship('Usuario', backref='audit_logs')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class RegraPrecificacao(db.Model):
    __tablename__ = 'regra_precificacao'
    id              = db.Column(db.Integer, primary_key=True)
    salao_id        = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=False, unique=True)
    limite_reservas = db.Column(db.Integer, nullable=False, default=1)
    valor_opcional  = db.Column(db.Numeric(10, 2), nullable=True)
    descricao       = db.Column(db.String(200), nullable=True)
    ativo           = db.Column(db.Boolean, default=True, nullable=False)
    criado_em       = db.Column(db.DateTime, default=datetime.utcnow)
    salao           = db.relationship('Salao', backref=db.backref('regra_precificacao', uselist=False))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class ReservaAnualUnidade(db.Model):
    __tablename__ = 'reserva_anual_unidade'
    id                   = db.Column(db.Integer, primary_key=True)
    condominio_id        = db.Column(db.Integer, db.ForeignKey('condominios.id'), nullable=False)
    # [FIX] cota era contada só por condominio_id+apartamento+ano — se um
    # condomínio tivesse 2 salões com RegraPrecificacao separada, usar a
    # isenção num consumia a cota do outro também. Adicionado pra escopar a
    # contagem por salão, batendo com o que a regra já era (por salão).
    salao_id             = db.Column(db.Integer, db.ForeignKey('saloes.id'), nullable=True)
    apartamento          = db.Column(db.String(20), nullable=False)
    ano                  = db.Column(db.Integer, nullable=False)
    reserva_id           = db.Column(db.Integer, db.ForeignKey('reservas.id'), nullable=False)
    valor_opcional_usado = db.Column(db.Boolean, default=False, nullable=False)
    criado_em            = db.Column(db.DateTime, default=datetime.utcnow)
    reserva              = db.relationship('Reserva', backref=db.backref('registro_anual', uselist=False))

    __table_args__ = (
        db.UniqueConstraint('condominio_id', 'apartamento', 'reserva_id', name='uq_reserva_anual_unidade'),
        db.Index('ix_rau_cond_apto_ano', 'condominio_id', 'apartamento', 'ano'),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def log_audit(usuario_id, acao, tabela, registro_id, dados_antes=None, dados_depois=None):
    try:
        from flask import request as _req
        ip = _req.remote_addr if _req else None
        ua = (_req.headers.get('User-Agent', '') if _req else '')[:500]
        entry = AuditLog(
            usuario_id  = usuario_id,
            acao        = acao,
            tabela      = tabela,
            registro_id = registro_id,
            dados_antes  = dados_antes,
            dados_depois = dados_depois,
            ip_address  = ip,
            user_agent  = ua,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        logging.error(f"Erro ao salvar audit log [{acao}]: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
