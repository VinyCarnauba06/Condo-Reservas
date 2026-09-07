from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app import db
from app.models import Usuario
from app.routes.auth import login_required, admin_required

usuarios_bp = Blueprint('usuarios', __name__)


@usuarios_bp.route('/usuarios')
@admin_required
def listar_usuarios():
    usuarios = Usuario.query.order_by(Usuario.nome).all()
    return render_template('usuarios.html', usuarios=usuarios)


@usuarios_bp.route('/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def novo_usuario():
    if request.method == 'POST':
        # [FEATURE] coordenador de fiscais — perfil novo, privilégios
        # elevados só dentro do escopo de vistoria (ver vistorias.py) +
        # acesso ao relatório de limpeza semanal (não ao recibo de zelador).
        _PERFIS_VALIDOS = {'admin', 'operador', 'fiscal', 'coordenador', 'fiscal_fixo'}
        nome   = request.form.get('nome', '').strip()
        login  = request.form.get('login', '').strip()
        senha  = request.form.get('senha', '')
        perfil_raw = request.form.get('perfil', 'operador')
        perfil = perfil_raw if perfil_raw in _PERFIS_VALIDOS else 'operador'

        erros = []
        if not nome:
            erros.append('Nome é obrigatório.')
        if not login:
            erros.append('Login é obrigatório.')
        if not senha:
            erros.append('Senha é obrigatória.')
        if Usuario.query.filter_by(login=login).first():
            erros.append('Login já está em uso.')

        if erros:
            for erro in erros:
                flash(erro, 'error')
            return render_template('form_usuario.html', usuario=None, modo='novo')

        usuario = Usuario(nome=nome, login=login, perfil=perfil)
        try:
            usuario.set_senha(senha)
        except ValueError as e:
            flash(f'Erro na senha: {e}', 'error')
            return render_template('form_usuario.html', usuario=None, modo='novo')

        try:
            db.session.add(usuario)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar usuário. O login pode já estar em uso.', 'error')
            return render_template('form_usuario.html', usuario=None, modo='novo')
        return redirect(url_for('usuarios.listar_usuarios'))

    return render_template('form_usuario.html', usuario=None, modo='novo')


@usuarios_bp.route('/usuarios/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(id):
    usuario = Usuario.query.get_or_404(id)

    if request.method == 'POST':
        _PERFIS_VALIDOS = {'admin', 'operador', 'fiscal', 'coordenador', 'fiscal_fixo'}
        nome   = request.form.get('nome', '').strip()
        login  = request.form.get('login', '').strip()
        perfil_raw = request.form.get('perfil', usuario.perfil)
        perfil = perfil_raw if perfil_raw in _PERFIS_VALIDOS else usuario.perfil
        ativo  = request.form.get('ativo') == '1'

        erros = []
        if not nome:
            erros.append('Nome é obrigatório.')
        if not login:
            erros.append('Login é obrigatório.')
        conflito = Usuario.query.filter_by(login=login).first()
        if conflito and conflito.id != id:
            erros.append('Login já está em uso.')

        if erros:
            for erro in erros:
                flash(erro, 'error')
            return render_template('form_usuario.html', usuario=usuario, modo='editar')

        usuario.nome   = nome
        usuario.login  = login
        usuario.perfil = perfil
        usuario.ativo  = ativo
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao salvar alterações.', 'error')
            return render_template('form_usuario.html', usuario=usuario, modo='editar')
        flash('Usuário atualizado.', 'success')
        return redirect(url_for('usuarios.listar_usuarios'))

    return render_template('form_usuario.html', usuario=usuario, modo='editar')


@usuarios_bp.route('/usuarios/<int:id>/senha', methods=['GET', 'POST'])
@admin_required
def trocar_senha(id):
    usuario = Usuario.query.get_or_404(id)

    if request.method == 'POST':
        senha    = request.form.get('senha', '')
        confirma = request.form.get('confirma', '')
    
        if senha != confirma:
            flash('As senhas não coincidem.', 'error')
            return render_template('form_usuario.html', usuario=usuario, modo='senha')

        try:
            usuario.set_senha(senha)
        except ValueError as e:
            flash(f'Erro na senha: {e}', 'error')
            return render_template('form_usuario.html', usuario=usuario, modo='senha')

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Erro ao alterar senha. Tente novamente.', 'error')
            return render_template('form_usuario.html', usuario=usuario, modo='senha')
        flash('Senha alterada com sucesso.', 'success')
        return redirect(url_for('usuarios.listar_usuarios'))

    return render_template('form_usuario.html', usuario=usuario, modo='senha')