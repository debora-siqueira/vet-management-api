from getpass import getpass

from app import app, db
from models import Usuario


def solicitar_senha(email):
    while True:
        senha = getpass(f'Senha inicial para {email} (mínimo 12 caracteres): ')
        confirmacao = getpass('Confirme a senha inicial: ')
        if senha != confirmacao:
            print('As senhas não conferem.')
        elif len(senha) < 12:
            print('A senha deve ter pelo menos 12 caracteres.')
        else:
            return senha


with app.app_context():
    usuarios = [
        {'nome': 'Carlos Lima', 'email': 'atendente@clinica.com', 'perfil': 'atendente'},
        {'nome': 'Dra. Ana Prado', 'email': 'vet@clinica.com', 'perfil': 'veterinario', 'crmv': 'MG-12345'}
    ]
    for d in usuarios:
        if not Usuario.query.filter_by(email=d['email']).first():
            u = Usuario(nome=d['nome'], email=d['email'], perfil=d['perfil'], crmv=d.get('crmv'))
            u.set_senha(solicitar_senha(d['email']))
            db.session.add(u)
    db.session.commit()
    print("Atendente e Veterinário adicionados com sucesso!")