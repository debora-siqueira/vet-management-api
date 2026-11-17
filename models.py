from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    perfil = db.Column(db.String(20), nullable=False)
    crmv = db.Column(db.String(20))
    ativo = db.Column(db.Boolean, default=True)

    def set_senha(self, s):
        self.senha_hash = generate_password_hash(s)

    def check_senha(self, s):
        return check_password_hash(self.senha_hash, s)


class Tutor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cpf = db.Column(db.String(14), unique=True, nullable=False)
    telefone = db.Column(db.String(20))
    endereco = db.Column(db.String(200))
    email = db.Column(db.String(120))
    ativo = db.Column(db.Boolean, default=True)


class Pet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    raca = db.Column(db.String(80))
    sexo = db.Column(db.String(10))
    idade = db.Column(db.Integer)
    peso = db.Column(db.Float)
    alergias = db.Column(db.String(200))
    ativo = db.Column(db.Boolean, default=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey('tutor.id'), nullable=False)

    tutor = db.relationship('Tutor', backref='pets')


class Consulta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pet_id = db.Column(db.Integer, db.ForeignKey('pet.id'), nullable=False)
    vet_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    data = db.Column(db.String(10), nullable=False)
    hora = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(20), default='Agendada')

    pet = db.relationship('Pet')
    vet = db.relationship('Usuario')

    __table_args__ = (
        db.Index('uq_vet_horario', 'vet_id', 'data', 'hora',
                 unique=True, sqlite_where=db.text("status != 'Cancelada'")),
        db.Index('ix_consulta_data_hora', 'data', 'hora'),
        db.Index('ix_consulta_status_data_hora', 'status', 'data', 'hora'),
        db.Index('ix_consulta_vet_status_data_hora', 'vet_id', 'status', 'data', 'hora'),
        db.Index('ix_consulta_pet_data_hora', 'pet_id', 'data', 'hora'),
    )


class Prontuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consulta_id = db.Column(db.Integer, db.ForeignKey('consulta.id'), nullable=False)
    data = db.Column(db.String(10), nullable=False, default=lambda: date.today().isoformat())
    sintomas = db.Column(db.Text, nullable=False)
    observacoes = db.Column(db.Text)
    diagnostico = db.Column(db.Text, nullable=False)
    procedimentos = db.Column(db.Text, nullable=False)
    medicacoes = db.Column(db.Text)
    orientacoes = db.Column(db.Text)

    consulta = db.relationship('Consulta', backref=db.backref('prontuario', uselist=False))

    __table_args__ = (
        db.Index('uq_prontuario_consulta_id', 'consulta_id', unique=True),
    )


class RascunhoProntuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    consulta_id = db.Column(db.Integer, db.ForeignKey('consulta.id'), nullable=False)
    dados = db.Column(db.Text, nullable=False)
    expira_em = db.Column(db.DateTime, nullable=False)
