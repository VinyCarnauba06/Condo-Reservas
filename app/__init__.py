import os
import logging
from datetime import timedelta
from flask import Flask, render_template, session, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_compress import Compress
from dotenv import load_dotenv


load_dotenv(override=True)

db      = SQLAlchemy()
csrf    = CSRFProtect()
migrate = Migrate()

os.makedirs("logs", exist_ok=True)

from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=5)
handler.setLevel(logging.ERROR)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

# [FIX] Railway tem filesystem efêmero — logs/app.log some a cada deploy/restart
# e, sem handler de stdout, os logging.error() nem apareciam no painel da Railway
# (que só captura stdout/stderr do container). StreamHandler garante visibilidade
# imediata em produção; o FileHandler continua útil só pra debug local.
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.ERROR)
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.getLogger().addHandler(handler)
logging.getLogger().addHandler(stream_handler)
logging.getLogger().setLevel(logging.ERROR)

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# [FIX] memory:// não é compartilhado entre workers Gunicorn — cada worker
# tem seu próprio contador, então "5 por minuto" na prática vira 5×N_workers.
# RATELIMIT_STORAGE_URI (se setada explicitamente) tem prioridade; senão usa
# REDIS_URL (já usado por outras partes do projeto) pra ativar storage
# compartilhado; sem nenhuma das duas, cai em memory:// com warning — ok só
# em dev/1 worker, mas visível em log caso aconteça sem querer em produção.
_ratelimit_storage_uri = os.getenv('RATELIMIT_STORAGE_URI') or os.getenv('REDIS_URL')
if not _ratelimit_storage_uri:
    logging.warning(
        "RATELIMIT_STORAGE_URI/REDIS_URL não configurada — rate limiting caindo em "
        "memory://, que não é compartilhado entre workers Gunicorn (limite efetivo "
        "vira N x o configurado, N = nº de workers). Configure REDIS_URL em produção."
    )
    _ratelimit_storage_uri = 'memory://'

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["40000 per day", "500 per hour"],
    storage_uri=_ratelimit_storage_uri
)

def _validate_encryption_key():
    key = os.getenv('ENCRYPTION_KEY')
    placeholders = {'sua-chave-aqui', 'change-me', 'changeme', 'secret', 'dev-key', 'chave-de-teste'}
    if not key or len(key) < 32 or key.lower() in placeholders:
        raise ValueError(
            "❌ ENCRYPTION_KEY ausente, curta (<32 chars) ou é um valor placeholder conhecido\n"
            "Execute: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "Cole o resultado em ENCRYPTION_KEY no seu .env"
        )

def _validate_secret_key():
    key = os.getenv('SECRET_KEY')
    placeholders = {'sua-chave-secreta', 'change-me', 'changeme', 'secret', 'dev-key', 'condoreservas-dev-key'}
    if not key or len(key) < 32 or key.lower() in placeholders:
        raise ValueError(
            "❌ SECRET_KEY ausente, curta (<32 chars) ou é um valor placeholder conhecido\n"
            "Execute: python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "Cole o resultado em SECRET_KEY no seu .env"
        )
    return key

def create_app():
    _validate_encryption_key()
    app = Flask(__name__)
    app.config['SECRET_KEY'] = _validate_secret_key()
    limiter.init_app(app)
    app.config['RATELIMIT_ENABLED'] = True
    
    from flask_limiter.errors import RateLimitExceeded
    
    @app.errorhandler(RateLimitExceeded)
    def handle_ratelimit(e):
        return {'erro': 'Muitas tentativas. Aguarde 15 minutos.'}, 429
    
    _base_dir    = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        if os.getenv('FLASK_ENV') == 'production':
            raise ValueError(
                "❌ DATABASE_URL ausente em produção — SQLite no Railway usa filesystem "
                "efêmero e os dados seriam perdidos a cada deploy. Configure DATABASE_URL."
            )
        database_url = f"sqlite:///{_base_dir}/instance/condoreservas.db"
        logging.warning(f"DATABASE_URL não definida — usando SQLite local ({database_url}). Só aceitável fora de produção.")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config['TEMPLATES_AUTO_RELOAD']          = True
    app.config['SQLALCHEMY_DATABASE_URI']        = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if database_url.startswith("sqlite"):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"connect_args": {"timeout": 15}}
    else:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            "connect_args":  {"connect_timeout": 15},
            "pool_size":     5,
            "max_overflow":  10,
            "pool_recycle":  1800,
            "pool_pre_ping": True,
        }
    app.config['PERMANENT_SESSION_LIFETIME']     = timedelta(hours=8)
    app.config['SESSION_COOKIE_HTTPONLY']        = True
    app.config['SESSION_COOKIE_SAMESITE']        = 'Lax'
    _flask_env = (os.getenv('FLASK_ENV') or '').strip().lower()
    if _flask_env not in ('production', 'development'):
        raise ValueError(
            f"❌ FLASK_ENV={os.getenv('FLASK_ENV')!r} inválido — precisa ser exatamente "
            "'production' ou 'development'. Corrija a env var (Railway/deploy)."
        )
    app.config['SESSION_COOKIE_SECURE']          = _flask_env == 'production'
    app.config['WTF_CSRF_TIME_LIMIT']            = 28800
    # [AJUSTE] subido de 12MB -> 20MB a pedido do Viny (mais folga pra
    # cadastrar foto de vistoria). Cobre o pior caso de 6 fotos x 2MB
    # decodificado (_MAX_FOTO_LEN em vistorias.py) + 2 assinaturas, já
    # inflado pelo base64 (~16-17MB), com margem. Memória é transitória por
    # request (buffer vira bytes no Postgres e é liberado); com Gunicorn
    # -w 2 --threads 4 o pior caso teórico simultâneo é ~136MB, tranquilo
    # pro volume real de fiscais enviando vistoria.
    app.config['MAX_CONTENT_LENGTH']             = 20 * 1024 * 1024

    # [FIX] Werkzeug 2.3+ tem um limite SEPARADO de MAX_CONTENT_LENGTH: cada
    # campo individual do form (não-arquivo) é capado em 500KB por padrão
    # (MAX_FORM_MEMORY_SIZE). Foto/assinatura em base64 vai como campo de
    # texto comum (foto_b64, assinatura_fiscal, assinatura_requerente), não
    # como arquivo — uma foto sozinha já estoura os 500KB e derruba o
    # request com 413 antes da rota rodar. Acompanha o MAX_CONTENT_LENGTH
    # acima (precisa ser >= pra não virar o novo teto efetivo).
    app.config['MAX_FORM_MEMORY_SIZE']            = 20 * 1024 * 1024

    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)
    Compress(app)

    from app.routes.condominios  import condominios_bp
    from app.routes.saloes       import saloes_bp
    from app.routes.reservas     import reservas_bp
    from app.routes.relatorios   import relatorios_bp
    from app.routes.auth         import auth_bp
    from app.routes.usuarios     import usuarios_bp
    from app.routes.vistorias    import vistorias_bp   # [TESTE] vistoria mobile
    from app.routes.fiscal_fixo  import fiscal_fixo_bp # [FEATURE] portal fiscal fixo
    app.register_blueprint(usuarios_bp)

    app.register_blueprint(condominios_bp)
    app.register_blueprint(saloes_bp)
    app.register_blueprint(reservas_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(vistorias_bp)
    app.register_blueprint(fiscal_fixo_bp)
     
 

    _FISCAL_ALLOWED = {
        'auth.logout', 'auth.login', 'static', 'health', 'sw_vistoria',
        'vistorias.painel_fiscal',       # [TESTE] painel simplificado — substitui o relatório semanal pro fiscal
        'vistorias.preencher_vistoria',  # [TESTE] vistoria mobile
        'vistorias.pegar_vistoria',      # [TESTE] reivindicar vistoria de fim de semana
        'vistorias.liberar_vistoria',    # [TESTE] devolver vistoria pro pool de fim de semana
        'relatorios.termo_individual',   # [TESTE] fiscal baixa o PDF do termo pra enviar por WhatsApp
        'relatorios.ver_fotos_vistoria', # [TESTE] fiscal confere as fotos que ele mesmo tirou
        'vistorias.ver_foto',            # [TESTE] imagem individual usada pela galeria acima
    }

    # [FEATURE] fiscal fixo — kiosk idêntico ao do fiscal, mas restrito ao portal
    _FISCAL_FIXO_ALLOWED = {
        'auth.logout', 'auth.login', 'static', 'health', 'sw_vistoria',
        # portal próprio
        'fiscal_fixo.portal',
        'fiscal_fixo.nova_reserva',
        'fiscal_fixo.editar_reserva',
        'fiscal_fixo.cancelar_reserva',
        'fiscal_fixo.gerar_comunicado',
        'fiscal_fixo.configuracoes',
        'fiscal_fixo.inventario',
        # rotas do operador reutilizadas (fiscal_fixo é redirecionado a elas)
        'reservas.nova_reserva',
        'reservas.editar_reserva',
        'reservas.cancelar_reserva',
        'reservas.ver_reserva',
        'reservas.api_morador',
        'reservas.api_reserva_anual_status',
        # documentos gerados via relatorios.py
        'relatorios.comunicado_individual',
        'relatorios.termo_individual',
        # vistoria/termo presencial — fiscal fixo faz pelo celular
        'vistorias.preencher_vistoria',
        'vistorias.ver_foto',
    }

    @app.before_request
    def fiscal_kiosk():
        perfil   = session.get('usuario_perfil')
        endpoint = request.endpoint or ''

        if perfil == 'fiscal':
            if endpoint not in _FISCAL_ALLOWED:
                return redirect(url_for('vistorias.painel_fiscal'))

        elif perfil == 'fiscal_fixo':
            if endpoint not in _FISCAL_FIXO_ALLOWED:
                return redirect(url_for('fiscal_fixo.portal'))

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        if _flask_env == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.tailwindcss.com cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' cdn.tailwindcss.com cdn.jsdelivr.net fonts.googleapis.com; "
            "img-src 'self' data:; "
            "font-src 'self' data: cdn.jsdelivr.net fonts.gstatic.com; "
            "connect-src 'self' cdn.jsdelivr.net"
        )
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    # [FIX] Service worker da vistoria offline precisa ser servido da RAIZ pra
    # ter escopo '/' (de /static/ o escopo seria /static/ e o SW não
    # controlaria nenhuma página — Background Sync e cache de navegação
    # morriam). O header Service-Worker-Allowed libera o scope amplo mesmo o
    # arquivo físico morando em /static/.
    @app.route("/sw-vistoria.js")
    def sw_vistoria():
        from flask import send_from_directory
        resp = send_from_directory(
            app.static_folder, "sw-vistoria.js",
            mimetype="application/javascript",
        )
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # Navegador sempre pede /favicon.ico sozinho, mesmo sem link nenhum pra
    # ele — só existe favicon.svg no projeto, então esse 404 era 100% ruído
    # no console. Redireciona pro SVG existente em vez de criar arquivo novo.
    @app.route("/favicon.ico")
    def favicon_ico():
        return redirect(url_for('static', filename='favicon.svg'))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_error(e):
        logging.error(f"Erro 500 em {request.method} {request.path}: {e}")
        return render_template("500.html"), 500
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("403.html"), 403

    # [FIX] CSRFError é uma HTTPException (400) — sem handler específico, caía
    # no @app.errorhandler(Exception) genérico abaixo, que devolve 500.html
    # com status 500. Status errado (era 400, sessão/token expirado — nada
    # "quebrou" no servidor) e sem contexto de qual request. Trata separado:
    # loga o request e manda pro login com mensagem clara.
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        logging.warning(f"CSRF falhou em {request.method} {request.path}: {e.description}")
        flash('Sua sessão expirou ou a página ficou aberta por muito tempo. Faça login novamente e tente de novo.', 'danger')
        return redirect(url_for('auth.login'))

    @app.errorhandler(Exception)
    def handle_exception(e):
        import traceback
        logging.error(f"Erro inesperado em {request.method} {request.path}: {e}\n{traceback.format_exc()}")
        return render_template("500.html"), 500

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        def _cleanup_audit_logs():
            with app.app_context():
                from datetime import datetime, timedelta, timezone
                from app.models import AuditLog
                from sqlalchemy import text

                _LOCK_KEY = 0x41474501
                acquired  = db.session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
                ).scalar()
                if not acquired:
                    return
                try:
                    cutoff  = datetime.now(timezone.utc) - timedelta(days=30)
                    deleted = AuditLog.query.filter(AuditLog.criado_em < cutoff).delete()
                    db.session.commit()
                    logging.info(f"Limpeza audit log: {deleted} registros deletados")
                finally:
                    db.session.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY}
                    )
                    db.session.commit()

        def _cleanup_vistoria_termos():
            # [TESTE] vistoria mobile — ver CHANGELOG_VISTORIA_MOBILE.md
            # Assinaturas (base64) são pesadas; PDF já foi baixado pelo operador/admin,
            # então não precisamos reter o termo digital indefinidamente no banco.
            with app.app_context():
                from datetime import datetime, timedelta
                from app.models import VistoriaTermo, VistoriaItem, VistoriaFoto
                from sqlalchemy import text

                _LOCK_KEY = 0x41474502
                acquired  = db.session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
                ).scalar()
                if not acquired:
                    return
                try:
                    cutoff = datetime.utcnow() - timedelta(days=30)
                    ids_antigos = [
                        row[0] for row in
                        db.session.query(VistoriaTermo.id)
                            .filter(VistoriaTermo.preenchido_em < cutoff).all()
                    ]
                    if ids_antigos:
                        VistoriaItem.query.filter(
                            VistoriaItem.vistoria_termo_id.in_(ids_antigos)
                        ).delete(synchronize_session=False)
                        # [FIX] bulk .delete() não dispara cascade do ORM —
                        # sem isso, fotos ficariam órfãs (FK pra termo já apagado).
                        VistoriaFoto.query.filter(
                            VistoriaFoto.vistoria_termo_id.in_(ids_antigos)
                        ).delete(synchronize_session=False)
                        VistoriaTermo.query.filter(
                            VistoriaTermo.id.in_(ids_antigos)
                        ).delete(synchronize_session=False)
                        db.session.commit()
                        logging.info(f"Limpeza vistoria_termos: {len(ids_antigos)} termos deletados")
                finally:
                    db.session.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY}
                    )
                    db.session.commit()

        def _backup_database_encrypted():
            # Advisory lock: sem isso, cada worker Gunicorn roda seu próprio
            # BackgroundScheduler e o backup dispara N vezes (N = nº de workers).
            with app.app_context():
                from sqlalchemy import text as _text
                _LOCK_KEY = 0x41474503
                acquired = db.session.execute(
                    _text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
                ).scalar()
                if not acquired:
                    return
                try:
                    import sys as _sys
                    import os as _os
                    _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..'))
                    _sys.path.insert(0, _root)
                    from backup_encrypted import run_backup
                    ok, msg = run_backup()
                    if ok:
                        logging.info(f"Backup criptografado executado com sucesso: {msg}")
                    else:
                        logging.error(f"Erro no backup criptografado: {msg}")
                except Exception as e:
                    logging.error(f"Erro ao executar backup criptografado: {e}")
                finally:
                    db.session.execute(
                        _text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY}
                    )
                    db.session.commit()

        from app.utils.backup import enviar_backup_agenda

        def _drp_agenda():
            # Mesmo motivo do lock acima: enviar_backup_agenda dispara e-mail/CSV,
            # sem lock isso duplicaria por worker.
            with app.app_context():
                from sqlalchemy import text as _text
                _LOCK_KEY = 0x41474504
                acquired = db.session.execute(
                    _text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
                ).scalar()
                if not acquired:
                    return
                try:
                    enviar_backup_agenda()
                finally:
                    db.session.execute(
                        _text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY}
                    )
                    db.session.commit()

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(_cleanup_audit_logs,        'cron', hour=23, minute=0)
        _scheduler.add_job(_cleanup_vistoria_termos,   'cron', hour=23, minute=15)
        _scheduler.add_job(_backup_database_encrypted,  'cron', hour=23, minute=30)
        _scheduler.add_job(_drp_agenda,                 'cron', hour=6,  minute=0)
        _scheduler.start()
    except Exception as e:
        logging.warning(f"APScheduler não iniciado: {e}")

    with app.app_context():
        from app import models
        # [FIX] db.create_all() removido — anti-pattern documentado no
        # CLAUDE.md do projeto. O Procfile já roda `flask db upgrade` antes
        # do gunicorn subir (fonte única de verdade do schema). create_all()
        # rodando em paralelo em cada worker não substitui migration pra
        # colunas novas em tabela existente (só cria tabela que não existe) e
        # mascara o caso real de risco: tabela nova sem migration seria
        # criada por baixo do Alembic, e a migration de verdade quebra depois
        # com "already exists".

        if database_url.startswith("sqlite"):
            from sqlalchemy import text
            db.session.execute(text("PRAGMA journal_mode=WAL"))
            db.session.commit()

        from app.models import Usuario

        if not Usuario.query.first():
            admin_password = os.getenv("ADMIN_PASSWORD")
            if not admin_password:
                if _flask_env == 'production':
                    raise ValueError(
                        "❌ ADMIN_PASSWORD ausente em produção — defina antes de subir a "
                        "conta admin inicial. Não gere/logue senha automaticamente."
                    )
                import secrets as _secrets
                admin_password = _secrets.token_urlsafe(9)
                print(f"[DEV SETUP] Conta 'admin' criada com senha gerada: {admin_password}")
            admin = Usuario(nome='Admin', login='admin', perfil='admin')
            admin.set_senha(admin_password)
            db.session.add(admin)
            db.session.commit()

        admin_existente = Usuario.query.filter_by(login='admin').first()
        if admin_existente and admin_existente.perfil != 'admin':
            admin_existente.perfil = 'admin'
            db.session.commit()

        if not Usuario.query.filter_by(login='fiscal').first():
            # [FIX] senha '123' hardcoded era um risco real — troca por senha
            # forte gerada no boot (ou FISCAL_PASSWORD, se setada). Só roda na
            # criação da conta, então NÃO reseta a senha de uma conta 'fiscal'
            # que já existir no banco.
            fiscal_password = os.getenv('FISCAL_PASSWORD')
            if not fiscal_password:
                if _flask_env == 'production':
                    raise ValueError(
                        "❌ FISCAL_PASSWORD ausente em produção — defina antes de subir a "
                        "conta fiscal inicial. Não gere/logue senha automaticamente."
                    )
                import secrets as _secrets
                fiscal_password = _secrets.token_urlsafe(9)
                print(f"[DEV SETUP] Conta 'fiscal' criada com senha gerada: {fiscal_password}")
            fiscal = Usuario(nome='Fiscal Fim de Semana', login='fiscal', perfil='fiscal')
            fiscal.set_senha(fiscal_password)
            db.session.add(fiscal)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    return app