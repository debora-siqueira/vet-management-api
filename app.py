import os
import secrets
import json
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from sqlalchemy import inspect, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from models import db, Usuario, Tutor, Pet, Consulta, Prontuario, RascunhoProntuario

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///vet.db')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('SECRET_KEY'):
    app.logger.warning('SECRET_KEY não configurada; usando chave temporária. Configure SECRET_KEY para produção.')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=10)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor, faça login para acessar a página.'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))


def perfil_requerido(*perfis):
    """Protege a rota por perfil (Seção 4.5 do documento)."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.perfil not in perfis:
                flash('Acesso negado: seu perfil não tem permissão para esta ação.', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def minutos_do_dia(valor):
    try:
        hora, minuto = (int(parte) for parte in valor.split(':'))
    except (AttributeError, ValueError):
        raise RuntimeError('Configure os horários da clínica no formato HH:MM.')
    if not 0 <= hora <= 23 or not 0 <= minuto <= 59:
        raise RuntimeError('Configure os horários da clínica no formato HH:MM.')
    return hora * 60 + minuto


INICIO_EXPEDIENTE = minutos_do_dia(os.environ.get('CLINIC_OPEN', '08:00'))
FIM_EXPEDIENTE = minutos_do_dia(os.environ.get('CLINIC_CLOSE', '18:00'))
try:
    INTERVALO_CONSULTA = int(os.environ.get('APPOINTMENT_INTERVAL_MINUTES', '30'))
except ValueError:
    raise RuntimeError('APPOINTMENT_INTERVAL_MINUTES deve ser um número inteiro.')
if INTERVALO_CONSULTA <= 0 or FIM_EXPEDIENTE - INICIO_EXPEDIENTE < INTERVALO_CONSULTA:
    raise RuntimeError('O intervalo e o expediente da clínica estão inválidos.')
HORARIOS = [
    f'{minuto // 60:02d}:{minuto % 60:02d}'
    for minuto in range(INICIO_EXPEDIENTE,
                        FIM_EXPEDIENTE - INTERVALO_CONSULTA + 1,
                        INTERVALO_CONSULTA)
]
PERFIS = ('administrador', 'atendente', 'veterinario')
CAMPOS_PRONTUARIO = ('sintomas', 'observacoes', 'diagnostico',
                     'procedimentos', 'medicacoes', 'orientacoes')
TAMANHO_PAGINA = 50
DURACAO_RASCUNHO_PRONTUARIO = timedelta(minutes=30)


def numero_pagina(parametro='page'):
    return max(request.args.get(parametro, 1, type=int) or 1, 1)


@app.template_filter('data_br')
def data_br(d):
    """2026-11-12 -> 12/11/2026"""
    return f'{d[8:10]}/{d[5:7]}/{d[:4]}' if d else ''


def formatar_cpf(cpf):
    """Retorna o CPF como 000.000.000-00 ou None se for inválido (RN08)."""
    d = ''.join(c for c in cpf if c.isdigit())
    if len(d) != 11 or d == d[0] * 11:
        return None
    for t in (9, 10):
        soma = sum(int(d[i]) * (t + 1 - i) for i in range(t))
        if (soma * 10 % 11) % 10 != int(d[t]):
            return None
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'


def horarios_ocupados_hoje():
    hoje = date.today().isoformat()
    query = (db.session.query(Consulta.vet_id, Consulta.hora)
             .filter(Consulta.status != 'Cancelada', Consulta.data == hoje))
    ocupados = {}
    for vet_id, hora in query:
        ocupados.setdefault(str(vet_id), set()).add(hora)
    return ocupados


def validar_horario(vet_id, data, hora):
    """Regras RN02 e RN07. Retorna a mensagem de erro ou None."""
    if not vet_id:
        return 'Selecione o veterinário.'
    if hora not in HORARIOS:
        return 'Horário fora do expediente configurado da clínica.'
    try:
        quando = datetime.strptime(f'{data} {hora}', '%Y-%m-%d %H:%M')
    except ValueError:
        return 'Informe uma data válida.'
    if quando < datetime.now():
        return 'Não é possível agendar em data ou horário passado.'
    return None


def email_em_uso(email, id_atual=None):
    return Usuario.query.filter(Usuario.email == email, Usuario.id != (id_atual or 0)).first() is not None


@app.before_request
def preservar_prontuario_apos_expiracao():
    if (request.endpoint != 'prontuario_form' or request.method != 'POST'
            or current_user.is_authenticated):
        return None

    consulta_id = (request.view_args or {}).get('consulta_id')
    consulta = db.session.get(Consulta, consulta_id) if consulta_id else None
    if not consulta or consulta.status == 'Cancelada':
        return None

    agora = datetime.utcnow()
    RascunhoProntuario.query.filter(
        RascunhoProntuario.expira_em <= agora
    ).delete(synchronize_session=False)
    token_anterior = session.pop('rascunho_prontuario_token', None)
    if token_anterior:
        RascunhoProntuario.query.filter_by(token=token_anterior).delete()

    dados = {'data': request.form.get('data', '').strip()}
    dados.update({
        campo: request.form.get(campo, '').strip()
        for campo in CAMPOS_PRONTUARIO
    })
    token = secrets.token_urlsafe(32)
    db.session.add(RascunhoProntuario(
        token=token,
        consulta_id=consulta.id,
        dados=json.dumps(dados, ensure_ascii=False),
        expira_em=agora + DURACAO_RASCUNHO_PRONTUARIO,
    ))
    db.session.commit()
    session['rascunho_prontuario_token'] = token
    flash('Sua sessão expirou. Faça login para recuperar o prontuário por até 30 minutos.', 'error')
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.ativo and usuario.check_senha(senha):
            login_user(usuario)
            session.permanent = True
            token = session.get('rascunho_prontuario_token')
            if token:
                rascunho = RascunhoProntuario.query.filter_by(token=token).first()
                if rascunho and rascunho.expira_em > datetime.utcnow():
                    consulta = db.session.get(Consulta, rascunho.consulta_id)
                    if consulta and consulta.vet_id == usuario.id and consulta.status != 'Cancelada':
                        return redirect(url_for(
                            'prontuario_form', consulta_id=consulta.id))
                    flash('Somente o veterinário responsável pode recuperar este prontuário.', 'error')
                else:
                    if rascunho:
                        db.session.delete(rascunho)
                    flash('O prazo para recuperar o prontuário expirou.', 'error')
                    session.pop('rascunho_prontuario_token', None)
            return redirect(url_for('index'))
        flash('E-mail ou senha incorretos.', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    hoje = date.today().isoformat()
    agora = datetime.now().strftime('%H:%M')
    agendadas = Consulta.query.filter_by(status='Agendada')
    consultas_hoje = (agendadas.filter_by(data=hoje)
                      .options(joinedload(Consulta.pet), joinedload(Consulta.vet))
                      .order_by(Consulta.hora).all())
    limite_proximas = (date.today() + timedelta(days=7)).isoformat()
    proximas = (agendadas.filter(Consulta.data > hoje,
                                 Consulta.data <= limite_proximas)
                .options(joinedload(Consulta.pet), joinedload(Consulta.vet))
                .order_by(Consulta.data, Consulta.hora).all())
    ocupados = horarios_ocupados_hoje()

    livres = []
    for v in Usuario.query.filter_by(perfil='veterinario', ativo=True).all():
        ocup = ocupados.get(str(v.id), set())
        livres.append((v, [h for h in HORARIOS if h not in ocup and h > agora]))

    return render_template('index.html',
                           total_agendadas=agendadas.count(),
                           consultas_hoje=consultas_hoje,
                           proximas=proximas,
                           consultas_realizadas=Consulta.query.filter_by(status='Realizada').count(),
                           livres=livres)


@app.route('/tutores-pets')
@login_required
def tutores_pets():
    editar = current_user.perfil == 'atendente'
    tutor_id = request.args.get('editar_tutor', type=int) or 0
    pet_id = request.args.get('editar_pet', type=int) or 0
    tutor_edit = (Tutor.query.filter_by(id=tutor_id, ativo=True).first()
                  if editar else None)
    pet_edit = (Pet.query.join(Tutor).filter(
        Pet.id == pet_id, Pet.ativo.is_(True), Tutor.ativo.is_(True)
    ).first() if editar else None)
    tutores_query = Tutor.query.options(selectinload(Tutor.pets))
    pets_query = Pet.query.options(joinedload(Pet.tutor))
    if current_user.perfil != 'administrador':
        tutores_query = tutores_query.filter(Tutor.ativo.is_(True))
        pets_query = pets_query.join(Tutor).filter(
            Pet.ativo.is_(True), Tutor.ativo.is_(True))
    return render_template('tutores_pets.html',
                           tutores=tutores_query.order_by(Tutor.nome).all(),
                           pets=pets_query.order_by(Pet.nome).all(),
                           tutor_edit=tutor_edit, pet_edit=pet_edit)


def ler_tutor(id_atual=None):
    """Lê e valida o formulário do tutor. Retorna (dados, erro)."""
    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()
    cpf = formatar_cpf(request.form.get('cpf', ''))
    dados = dict(nome=nome, telefone=telefone, cpf=cpf,
                 email=request.form.get('email', '').strip(),
                 endereco=request.form.get('endereco', '').strip())
    if not nome or not telefone:
        return dados, 'Informe nome e telefone.'
    if not cpf:
        return dados, 'CPF inválido.'
    if Tutor.query.filter(Tutor.cpf == cpf, Tutor.id != (id_atual or 0)).first():
        return dados, 'CPF já cadastrado.'
    return dados, None


@app.route('/tutores', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def cadastrar_tutor():
    dados, erro = ler_tutor()
    if erro:
        flash(erro, 'error')
    else:
        db.session.add(Tutor(**dados))
        db.session.commit()
        flash('Tutor cadastrado.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/tutores/<int:id>/editar', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def editar_tutor(id):
    t = db.get_or_404(Tutor, id)
    if not t.ativo:
        flash('Somente o administrador pode visualizar registros inativos.', 'error')
        return redirect(url_for('tutores_pets'))
    dados, erro = ler_tutor(id)
    if erro:
        flash(erro, 'error')
        return redirect(url_for('tutores_pets', editar_tutor=id))
    for k, v in dados.items():
        setattr(t, k, v)
    db.session.commit()
    flash('Tutor atualizado.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/tutores/<int:id>/excluir', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def excluir_tutor(id):
    tutor = db.get_or_404(Tutor, id)
    if not tutor.ativo:
        flash('Somente o administrador pode visualizar registros inativos.', 'error')
    else:
        possui_historico = (Consulta.query.join(Pet)
                            .filter(Pet.tutor_id == tutor.id).first())
        if possui_historico:
            tutor.ativo = False
            for pet in tutor.pets:
                pet.ativo = False
            flash('Há consultas vinculadas. Tutor e pets foram inativados para preservar o histórico.', 'error')
        else:
            for pet in tutor.pets:
                db.session.delete(pet)
            db.session.delete(tutor)
            flash('Tutor e pets excluídos; não havia consultas vinculadas.', 'ok')
        db.session.commit()
    return redirect(url_for('tutores_pets'))


@app.route('/tutores/<int:id>/reativar', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def reativar_tutor(id):
    db.get_or_404(Tutor, id).ativo = True
    db.session.commit()
    flash('Tutor reativado.', 'ok')
    return redirect(url_for('tutores_pets'))


def ler_pet():
    """Lê e valida o formulário do pet. Retorna (dados, erro)."""
    nome = request.form.get('nome', '').strip()
    tutor = db.session.get(Tutor, request.form.get('tutor_id', type=int) or 0)
    dados = dict(nome=nome, tutor_id=tutor.id if tutor else None,
                 raca=request.form.get('raca', '').strip(),
                 sexo=request.form.get('sexo', ''),
                 idade=request.form.get('idade', type=int),
                 peso=request.form.get('peso', type=float),
                 alergias=request.form.get('alergias', '').strip())
    if not nome or not tutor:
        return dados, 'Informe o nome do pet e selecione o tutor.'
    return dados, None


@app.route('/pets', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def cadastrar_pet():
    dados, erro = ler_pet()
    if erro:
        flash(erro, 'error')
    elif not db.session.get(Tutor, dados['tutor_id']).ativo:
        flash('Selecione um tutor ativo.', 'error')
    else:
        db.session.add(Pet(**dados))
        db.session.commit()
        flash('Pet cadastrado.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/pets/<int:id>/editar', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def editar_pet(id):
    p = db.get_or_404(Pet, id)
    if not p.ativo or not p.tutor.ativo:
        flash('Somente o administrador pode visualizar registros inativos.', 'error')
        return redirect(url_for('tutores_pets'))
    dados, erro = ler_pet()
    if erro:
        flash(erro, 'error')
        return redirect(url_for('tutores_pets', editar_pet=id))
    for k, v in dados.items():
        setattr(p, k, v)
    db.session.commit()
    flash('Pet atualizado.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/pets/<int:id>/excluir', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def excluir_pet(id):
    pet = db.get_or_404(Pet, id)
    if not pet.ativo or not pet.tutor.ativo:
        flash('Somente o administrador pode visualizar registros inativos.', 'error')
    elif Consulta.query.filter_by(pet_id=pet.id).first():
        pet.ativo = False
        db.session.commit()
        flash('Há consultas vinculadas. O pet foi inativado para preservar o histórico.', 'error')
    else:
        db.session.delete(pet)
        db.session.commit()
        flash('Pet excluído; não havia consultas vinculadas.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/pets/<int:id>/reativar', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def reativar_pet(id):
    p = db.get_or_404(Pet, id)
    if not p.tutor.ativo:
        flash('Reative primeiro o tutor deste pet.', 'error')
    else:
        p.ativo = True
        db.session.commit()
        flash('Pet reativado.', 'ok')
    return redirect(url_for('tutores_pets'))


@app.route('/agenda')
@login_required
def agenda():
    editando = None
    if current_user.perfil == 'atendente':
        c = db.session.get(Consulta, request.args.get('reagendar', type=int) or 0)
        editando = c if c and c.status == 'Agendada' else None

    anteriores = request.args.get('anteriores') == '1'
    busca_consulta = request.args.get('buscar_consulta', '').strip()
    return render_agenda(anteriores=anteriores, editando=editando,
                         busca_consulta=busca_consulta)


def render_agenda(anteriores=False, editando=None, form_data=None,
                  busca_consulta=''):
    consulta_query = (Consulta.query
                      .options(joinedload(Consulta.pet), joinedload(Consulta.vet)))
    if anteriores:
        consulta_query = consulta_query.filter(Consulta.data < date.today().isoformat())
        ordenacao = (Consulta.data.desc(), Consulta.hora.desc(), Consulta.id.desc())
    else:
        consulta_query = consulta_query.filter(Consulta.data >= date.today().isoformat())
        ordenacao = (Consulta.data, Consulta.hora, Consulta.id)

    consultas = (consulta_query.order_by(*ordenacao)
                 .paginate(page=numero_pagina(), per_page=TAMANHO_PAGINA, error_out=False))
    encontradas = None
    if busca_consulta:
        like = f'%{busca_consulta}%'
        encontradas = (Consulta.query
                       .join(Consulta.pet).join(Pet.tutor).join(Consulta.vet)
                       .options(joinedload(Consulta.pet), joinedload(Consulta.vet))
                       .filter(Consulta.status == 'Agendada')
                       .filter(or_(Pet.nome.ilike(like), Tutor.nome.ilike(like),
                                   Usuario.nome.ilike(like)))
                       .order_by(Consulta.data, Consulta.hora, Consulta.id)
                       .paginate(page=numero_pagina('busca_page'),
                                 per_page=TAMANHO_PAGINA, error_out=False))
    return render_template('agenda.html',
                           consultas=consultas,
                           pets=(Pet.query.options(joinedload(Pet.tutor))
                                 .filter(Pet.ativo.is_(True),
                                         Pet.tutor.has(Tutor.ativo.is_(True)))
                                 .order_by(Pet.nome).all()),
                           vets=Usuario.query.filter_by(perfil='veterinario', ativo=True).all(),
                           horarios=HORARIOS,
                           hoje=date.today().isoformat(),
                           editando=editando, anteriores=anteriores,
                           form_data=form_data or {}, busca_consulta=busca_consulta,
                           encontradas=encontradas)


def proximo_horario_livre(vet_id, data, hora='', ignorar_id=None):
    try:
        dia_inicial = date.fromisoformat(data)
    except ValueError:
        return None

    query = Consulta.query.filter(
        Consulta.vet_id == vet_id,
        Consulta.status != 'Cancelada',
        Consulta.data >= data)
    if ignorar_id:
        query = query.filter(Consulta.id != ignorar_id)
    ocupadas = {(consulta.data, consulta.hora) for consulta in query}
    ultimo_dia = max((date.fromisoformat(dia) for dia, _ in ocupadas),
                     default=dia_inicial)
    dia = dia_inicial
    agora = datetime.now()

    while dia <= ultimo_dia + timedelta(days=1):
        data_iso = dia.isoformat()
        for horario in HORARIOS:
            if dia == dia_inicial and hora and horario <= hora:
                continue
            if datetime.strptime(f'{data_iso} {horario}', '%Y-%m-%d %H:%M') < agora:
                continue
            if (data_iso, horario) not in ocupadas:
                return {'data': data_iso, 'hora': horario}
        dia += timedelta(days=1)
    return None


@app.route('/agenda/horarios-ocupados')
@login_required
@perfil_requerido('atendente')
def horarios_ocupados():
    vet_id = request.args.get('vet', type=int)
    data = request.args.get('data', '')
    ignorar_id = request.args.get('ignorar_id', type=int)
    if not vet_id or not data:
        return jsonify(error='Informe o veterinário e a data.'), 400
    try:
        data_validada = date.fromisoformat(data)
    except ValueError:
        return jsonify(error='Informe uma data válida.'), 400
    if data_validada.isoformat() != data:
        return jsonify(error='Informe uma data válida.'), 400

    vet = Usuario.query.filter_by(id=vet_id, perfil='veterinario', ativo=True).first()
    if not vet:
        return jsonify(error='Veterinário inválido.'), 400

    if ignorar_id:
        consulta = db.session.get(Consulta, ignorar_id)
        if not consulta or consulta.status != 'Agendada':
            return jsonify(error='Consulta para reagendamento inválida.'), 400

    query = (db.session.query(Consulta.hora)
             .filter(Consulta.vet_id == vet_id, Consulta.data == data,
                     Consulta.status != 'Cancelada'))
    if ignorar_id:
        query = query.filter(Consulta.id != ignorar_id)
    horario_selecionado = request.args.get('hora', '')
    sugestao = proximo_horario_livre(
        vet_id, data, horario_selecionado, ignorar_id=ignorar_id)
    return jsonify(horarios=[hora for (hora,) in query], sugestao=sugestao)


@app.route('/agenda/agendar', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def agendar():
    pet_id = request.form.get('pet', type=int)
    vet_id = request.form.get('vet', type=int)
    data = request.form.get('data', '')
    hora = request.form.get('hora', '')

    erro = 'Selecione o animal e o veterinário.' if not pet_id or not vet_id \
        else validar_horario(vet_id, data, hora)
    if erro:
        flash(erro, 'error')
        return render_agenda(form_data=request.form)

    db.session.add(Consulta(pet_id=pet_id, vet_id=vet_id, data=data, hora=hora))
    try:
        db.session.commit()
        flash('Consulta agendada.', 'ok')
    except IntegrityError:
        db.session.rollback()
        sugestao = proximo_horario_livre(vet_id, data, hora)
        mensagem = 'Horário ocupado. Escolha outro horário.'
        if sugestao:
            mensagem += (
                f" Próximo horário livre: "
                f"{data_br(sugestao['data'])} às {sugestao['hora']}."
            )
        flash(mensagem, 'error')
        return render_agenda(form_data=request.form)
    return redirect(url_for('agenda'))


@app.route('/agenda/<int:id>/reagendar', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def reagendar(id):
    c = db.get_or_404(Consulta, id)
    if c.status != 'Agendada':
        flash('Só é possível reagendar consultas agendadas.', 'error')
        return redirect(url_for('agenda'))

    vet_id = request.form.get('vet', type=int)
    data = request.form.get('data', '')
    hora = request.form.get('hora', '')
    erro = validar_horario(vet_id, data, hora)
    if erro:
        flash(erro, 'error')
        return render_agenda(editando=c, form_data=request.form,
                             busca_consulta=request.form.get('buscar_consulta', '').strip())

    c.vet_id, c.data, c.hora = vet_id, data, hora
    try:
        db.session.commit()
        flash('Consulta reagendada.', 'ok')
    except IntegrityError:
        db.session.rollback()
        sugestao = proximo_horario_livre(vet_id, data, hora, ignorar_id=id)
        mensagem = 'Horário ocupado. Escolha outro horário.'
        if sugestao:
            mensagem += (
                f" Próximo horário livre: "
                f"{data_br(sugestao['data'])} às {sugestao['hora']}."
            )
        flash(mensagem, 'error')
        return render_agenda(editando=c, form_data=request.form,
                             busca_consulta=request.form.get('buscar_consulta', '').strip())
    return redirect(url_for('agenda'))


@app.route('/agenda/<int:id>/cancelar', methods=['POST'])
@login_required
@perfil_requerido('atendente')
def cancelar(id):
    c = db.get_or_404(Consulta, id)
    if c.status == 'Agendada':
        c.status = 'Cancelada'
        db.session.commit()
        flash('Consulta cancelada; horário liberado.', 'ok')
    return redirect(url_for('agenda'))


@app.route('/prontuario')
@login_required
@perfil_requerido('veterinario')
def prontuario():
    minhas = Consulta.query.filter(Consulta.vet_id == current_user.id)
    pendentes = (minhas.filter(Consulta.status == 'Agendada')
                 .options(joinedload(Consulta.pet))
                 .order_by(Consulta.data, Consulta.hora, Consulta.id)
                 .paginate(page=numero_pagina('pendentes_page'),
                           per_page=TAMANHO_PAGINA, error_out=False))
    feitas = (minhas.filter(Consulta.status == 'Realizada')
              .join(Consulta.prontuario)
              .options(joinedload(Consulta.pet), joinedload(Consulta.prontuario))
              .order_by(Prontuario.data.desc(), Consulta.hora.desc(), Consulta.id.desc())
              .paginate(page=numero_pagina('feitas_page'),
                        per_page=TAMANHO_PAGINA, error_out=False))
    return render_template('prontuario.html',
                           pendentes=pendentes, feitas=feitas)


@app.route('/prontuario/<int:consulta_id>', methods=['GET', 'POST'])
@login_required
@perfil_requerido('veterinario')
def prontuario_form(consulta_id):
    c = db.get_or_404(Consulta, consulta_id)
    if c.vet_id != current_user.id:
        flash('Esta consulta é de outro veterinário.', 'error')
        return redirect(url_for('prontuario'))
    if c.status == 'Cancelada':
        flash('Consulta cancelada não tem prontuário.', 'error')
        return redirect(url_for('prontuario'))

    p = c.prontuario
    if request.method == 'POST':
        f = {k: request.form.get(k, '').strip() for k in CAMPOS_PRONTUARIO}
        f['data'] = request.form.get('data', '').strip()
        try:
            data_atendimento = date.fromisoformat(f['data'])
            if data_atendimento.isoformat() != f['data']:
                raise ValueError
        except ValueError:
            data_atendimento = None
        if not f['data'] or not data_atendimento:
            flash('Informe uma data válida para o atendimento.', 'error')
        elif not f['sintomas'] or not f['diagnostico'] or not f['procedimentos']:
            flash('Preencha os campos obrigatórios (*).', 'error')
        else:
            if p is None:
                p = Prontuario(consulta_id=c.id)
                db.session.add(p)
            p.data = f.pop('data')
            for k, v in f.items():
                setattr(p, k, v)
            c.status = 'Realizada'
            token = session.get('rascunho_prontuario_token')
            if token:
                RascunhoProntuario.query.filter_by(
                    token=token, consulta_id=c.id
                ).delete()
                session.pop('rascunho_prontuario_token', None)
            db.session.commit()
            flash('Prontuário salvo; consulta marcada como Realizada.', 'ok')
            return redirect(url_for('prontuario'))
    else:
        f = {k: (getattr(p, k) or '') if p else '' for k in CAMPOS_PRONTUARIO}
        f['data'] = p.data if p else c.data
        token = session.get('rascunho_prontuario_token')
        if token:
            rascunho = RascunhoProntuario.query.filter_by(token=token).first()
            if rascunho and rascunho.expira_em > datetime.utcnow():
                if rascunho.consulta_id == c.id:
                    f.update(json.loads(rascunho.dados))
                    flash('Rascunho recuperado. Revise os dados e salve o prontuário.', 'ok')
            else:
                if rascunho:
                    db.session.delete(rascunho)
                session.pop('rascunho_prontuario_token', None)
                flash('O prazo para recuperar o prontuário expirou.', 'error')

    historico = (Prontuario.query.join(Consulta)
                 .options(joinedload(Prontuario.consulta).joinedload(Consulta.vet))
                 .filter(Consulta.pet_id == c.pet_id, Consulta.id != c.id)
                 .order_by(Prontuario.data.desc(), Consulta.hora.desc(), Consulta.id.desc())
                 .all())
    return render_template('prontuario_form.html', c=c, f=f, historico=historico)


@app.route('/historico')
@login_required
@perfil_requerido('veterinario')
def historico():
    pet = db.session.get(Pet, request.args.get('pet', type=int) or 0)
    registros = []
    if pet:
        registros = (Prontuario.query.join(Consulta)
                     .options(joinedload(Prontuario.consulta).joinedload(Consulta.vet))
                     .filter(Consulta.pet_id == pet.id)
                     .order_by(Prontuario.data.desc(), Consulta.hora.desc(), Consulta.id.desc())
                     .paginate(page=numero_pagina(), per_page=TAMANHO_PAGINA, error_out=False))
    return render_template('historico.html',
                           pets=Pet.query.options(joinedload(Pet.tutor)).order_by(Pet.nome).all(),
                           pet=pet, registros=registros)


@app.route('/busca')
@login_required
def busca():
    q = request.args.get('q', '').strip()
    pets, tutores = [], []
    if q:
        like = f'%{q}%'
        pets_query = (Pet.query.options(joinedload(Pet.tutor)).join(Tutor)
                      .filter(or_(Pet.nome.ilike(like), Tutor.nome.ilike(like))))
        tutores_query = (Tutor.query.options(selectinload(Tutor.pets))
                         .filter(Tutor.nome.ilike(like)))
        if current_user.perfil != 'administrador':
            pets_query = pets_query.filter(
                Pet.ativo.is_(True), Tutor.ativo.is_(True))
            tutores_query = tutores_query.filter(Tutor.ativo.is_(True))
        pets = pets_query.order_by(Pet.nome).all()
        tutores = tutores_query.order_by(Tutor.nome).all()
    return render_template('busca.html', q=q, pets=pets, tutores=tutores)


def ler_usuario(id_atual=None):
    """Lê e valida o formulário de usuário. Retorna (dados, senha, erro)."""
    dados = dict(nome=request.form.get('nome', '').strip(),
                 email=request.form.get('email', '').strip().lower(),
                 perfil=request.form.get('perfil', ''),
                 crmv=request.form.get('crmv', '').strip() or None)
    senha = request.form.get('senha', '')
    if not dados['nome'] or not dados['email']:
        erro = 'Informe nome e e-mail.'
    elif dados['perfil'] not in PERFIS:
        erro = 'Perfil inválido.'
    elif dados['perfil'] == 'veterinario' and not dados['crmv']:
        erro = 'Veterinário exige CRMV.'
    elif email_em_uso(dados['email'], id_atual):
        erro = 'E-mail já cadastrado.'
    elif (id_atual is None or senha) and len(senha) < 12:
        erro = 'A senha deve ter pelo menos 12 caracteres.'
    else:
        erro = None
    if dados['perfil'] != 'veterinario':
        dados['crmv'] = None
    return dados, senha, erro


@app.route('/usuarios')
@login_required
@perfil_requerido('administrador')
def usuarios():
    editando = db.session.get(Usuario, request.args.get('editar', type=int) or 0)
    return render_template('usuarios.html', perfis=PERFIS, editando=editando,
                           usuarios=Usuario.query.order_by(Usuario.nome).all())


@app.route('/usuarios', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def cadastrar_usuario():
    dados, senha, erro = ler_usuario()
    if erro:
        flash(erro, 'error')
    else:
        u = Usuario(**dados)
        u.set_senha(senha)
        db.session.add(u)
        db.session.commit()
        flash('Usuário cadastrado.', 'ok')
    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/editar', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def editar_usuario(id):
    u = db.get_or_404(Usuario, id)
    dados, senha, erro = ler_usuario(id)
    if erro:
        flash(erro, 'error')
        return redirect(url_for('usuarios', editar=id))
    if u.id == current_user.id:
        dados['perfil'] = u.perfil
    for k, v in dados.items():
        setattr(u, k, v)
    if senha:
        u.set_senha(senha)
    db.session.commit()
    flash('Usuário atualizado.', 'ok')
    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/inativar', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def inativar_usuario(id):
    u = db.get_or_404(Usuario, id)
    if u.id == current_user.id:
        flash('Você não pode inativar a própria conta.', 'error')
    else:
        u.ativo = False
        db.session.commit()
        flash('Usuário inativado.', 'ok')
    return redirect(url_for('usuarios'))


@app.route('/usuarios/<int:id>/reativar', methods=['POST'])
@login_required
@perfil_requerido('administrador')
def reativar_usuario(id):
    db.get_or_404(Usuario, id).ativo = True
    db.session.commit()
    flash('Usuário reativado.', 'ok')
    return redirect(url_for('usuarios'))


@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not nome or not email:
            flash('Informe nome e e-mail.', 'error')
        elif email_em_uso(email, current_user.id):
            flash('E-mail já cadastrado.', 'error')
        else:
            u = db.session.get(Usuario, current_user.id)
            u.nome, u.email = nome, email
            db.session.commit()
            flash('Perfil atualizado.', 'ok')
        return redirect(url_for('perfil'))
    return render_template('perfil.html')


@app.route('/perfil/senha', methods=['POST'])
@login_required
def alterar_senha():
    u = db.session.get(Usuario, current_user.id)
    atual = request.form.get('atual', '')
    nova = request.form.get('nova', '')
    confirma = request.form.get('confirma', '')
    if not u.check_senha(atual):
        flash('Senha atual incorreta.', 'error')
    elif len(nova) < 12:
        flash('A nova senha deve ter pelo menos 12 caracteres.', 'error')
    elif nova != confirma:
        flash('A confirmação não confere com a nova senha.', 'error')
    else:
        u.set_senha(nova)
        db.session.commit()
        flash('Senha alterada.', 'ok')
    return redirect(url_for('perfil'))


with app.app_context():
    db.create_all()
    with db.engine.begin() as conexao:
        prontuario_colunas = {
            coluna['name'] for coluna in inspect(conexao).get_columns('prontuario')
        }
        if 'data' not in prontuario_colunas:
            conexao.exec_driver_sql('ALTER TABLE prontuario ADD COLUMN data VARCHAR(10)')
        conexao.exec_driver_sql(
            'UPDATE prontuario SET data = '
            '(SELECT consulta.data FROM consulta '
            'WHERE consulta.id = prontuario.consulta_id) '
            'WHERE data IS NULL'
        )
    for tabela in (Consulta.__table__, Prontuario.__table__):
        for indice in tabela.indexes:
            indice.create(bind=db.engine, checkfirst=True)
    db.Index('ix_prontuario_consulta_id', Prontuario.consulta_id).drop(
        bind=db.engine, checkfirst=True)

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
