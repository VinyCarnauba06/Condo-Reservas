import logging
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, abort
from app import db
from app.models import Usuario, log_audit
from app import limiter
from flask_limiter.util import get_remote_address

auth_bp = Blueprint('auth', __name__)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('auth.login'))
        if session.get('usuario_perfil') != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=get_remote_address)
def login():
    if 'usuario_id' in session:
        # [FEATURE] fiscal fixo — portal dedicado ao condomínio vinculado
        if session.get('usuario_perfil') == 'fiscal_fixo':
            return redirect(url_for('fiscal_fixo.portal'))
        # [FEATURE] coordenador usa o mesmo painel mobile do fiscal (é uma
        # extensão do fluxo dele, com privilégios a mais — ver vistorias.py)
        if session.get('usuario_perfil') in ('fiscal', 'coordenador'):
            return redirect(url_for('vistorias.painel_fiscal'))
        return redirect(url_for('condominios.home'))
    if request.method == 'POST':
        try:
            login_val = request.form.get('login', '').strip()
            senha     = request.form.get('senha', '')
            lembrar   = request.form.get('lembrar') == 'on'
            usuario   = Usuario.query.filter_by(login=login_val, ativo=True).first()
            if usuario and usuario.checar_senha(senha):
                session.clear()
                session.permanent          = lembrar
                session['usuario_id']      = usuario.id
                session['usuario_nome']    = usuario.nome
                session['usuario_login']   = usuario.login
                session['usuario_perfil']  = usuario.perfil
                log_audit(
                    usuario.id, 'login_sucesso', 'usuarios', usuario.id,
                    dados_depois={'login': login_val, 'ip': request.remote_addr}
                )
                if usuario.perfil == 'fiscal_fixo':
                    return redirect(url_for('fiscal_fixo.portal'))
                if usuario.perfil in ('fiscal', 'coordenador'):
                    return redirect(url_for('vistorias.painel_fiscal'))
                return redirect(url_for('condominios.home'))
            log_audit(
                usuario.id if usuario else None,
                'login_falhou', 'usuarios',
                usuario.id if usuario else None,
                dados_depois={'login': login_val, 'ip': request.remote_addr}
            )
            return render_template('login.html', erro='Login ou senha inválidos')
        except Exception as e:
            import traceback
            logging.error(f"Erro no login: {e}\n{traceback.format_exc()}")
            return render_template('login.html', erro='Erro interno. Tente novamente.')
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))