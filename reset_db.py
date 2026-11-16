from app import app, db
from models import Usuario, Tutor, Pet, Consulta, Prontuario

with app.app_context():
    # Apaga as tabelas antigas e recria do zero
    db.drop_all()
    db.create_all()
    
    # Cria os 3 usuários oficiais
    usuarios = [
        {'nome': 'Marta Souza', 'email': 'admin@clinica.com', 'senha': '123456', 'perfil': 'administrador'},
        {'nome': 'Carlos Lima', 'email': 'atendente@clinica.com', 'senha': '123456', 'perfil': 'atendente'},
        {'nome': 'Dra. Ana Prado', 'email': 'vet@clinica.com', 'senha': '123456', 'perfil': 'veterinario', 'crmv': 'MG-12345'}
    ]
    
    for d in usuarios:
        u = Usuario(nome=d['nome'], email=d['email'], perfil=d['perfil'], crmv=d.get('crmv'))
        u.set_senha(d['senha']) # Criptografa a senha
        db.session.add(u)
        
    db.session.commit()
    print("Banco recriado! Os 3 acessos foram configurados com sucesso.")
