"""
database.py
============
Configuração da engine SQLite e utilitários de sessão (SQLModel).

O arquivo do banco pode ser sobrescrito via variável de ambiente
DATABASE_FILE, útil para separar ambientes de desenvolvimento/teste/produção.
"""

import os
from typing import Generator

from sqlmodel import SQLModel, Session, create_engine

# Define o diretório de dados e garante que ele exista
DATABASE_DIR = "data"
os.makedirs(DATABASE_DIR, exist_ok=True)

# Puxa a variável de ambiente ou usa o caminho relativo dentro de data/
DATABASE_FILE = os.getenv("DATABASE_FILE", os.path.join(DATABASE_DIR, "tutores.db"))
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# check_same_thread=False é necessário pois o FastAPI pode acessar a mesma
# conexão a partir de threads diferentes (worker pool do Starlette/Uvicorn).
connect_args = {"check_same_thread": False}

engine = create_engine(
  DATABASE_URL,
  echo=False,
  connect_args=connect_args,
)


def create_db_and_tables() -> None:
  """Cria todas as tabelas registradas nos metadados do SQLModel."""
  SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
  """
  Dependency do FastAPI que fornece uma sessão de banco de dados por
  requisição, garantindo o fechamento correto da conexão ao final.
  """
  with Session(engine) as session:
    yield session