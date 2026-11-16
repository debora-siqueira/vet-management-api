from getpass import getpass

from app import app, db
from models import Usuario, Tutor, Pet, Consulta, Prontuario


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


if input('Isto apagará todos os dados. Digite APAGAR TODOS OS DADOS para continuar: ') != 'APAGAR TODOS OS DADOS':
    raise SystemExit('Operação cancelada.')

usuarios = [
    {'nome': 'Marta Souza', 'email': 'admin@clinica.com', 'perfil': 'administrador'},
    {'nome': 'Carlos Lima', 'email': 'atendente@clinica.com', 'perfil': 'atendente'},
    {'nome': 'Dra. Ana Prado', 'email': 'vet@clinica.com', 'perfil': 'veterinario', 'crmv': 'MG-12345'}
]
for usuario in usuarios:
    usuario['senha'] = solicitar_senha(usuario['email'])

with app.app_context():
    db.drop_all()
    db.create_all()
    for d in usuarios:
        u = Usuario(nome=d['nome'], email=d['email'], perfil=d['perfil'], crmv=d.get('crmv'))
        u.set_senha(d['senha'])
        db.session.add(u)

    db.session.commit()
    print("Banco recriado! Os 3 acessos foram configurados com sucesso.")
