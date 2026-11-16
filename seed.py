from app import app, db
from models import Usuario

with app.app_context():
    usuarios = [
        {'nome': 'Carlos Lima', 'email': 'atendente@clinica.com', 'senha': '123456', 'perfil': 'atendente'},
        {'nome': 'Dra. Ana Prado', 'email': 'vet@clinica.com', 'senha': '123456', 'perfil': 'veterinario', 'crmv': 'MG-12345'}
    ]
    for d in usuarios:
        if not Usuario.query.filter_by(email=d['email']).first():
            u = Usuario(nome=d['nome'], email=d['email'], perfil=d['perfil'], crmv=d.get('crmv'))
            u.set_senha(d['senha'])
            db.session.add(u)
    db.session.commit()
    print("Atendente e Veterinário adicionados com sucesso!")