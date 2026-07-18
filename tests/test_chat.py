import os

# Garante que o Pydantic AI use o modelo mock "test" e não exija chaves da OpenAI no import
os.environ["AGENT_MODEL"] = "test"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from app.main import app
from app.database import get_session
from app.models import Tutor

# 1. Setup de um Banco de Dados SQLite em Memória exclusivo para os testes
# Isso garante isolamento total e evita sujeira no seu arquivo data/tutores.db
engine_teste = create_engine("sqlite://", connect_args={"check_same_thread": False})


@pytest.fixture(name="session")
def session_fixture():
    SQLModel.metadata.create_all(engine_teste)
    with Session(engine_teste) as session:
        yield session
    SQLModel.metadata.drop_all(engine_teste)


@pytest.fixture(name="client")
def client_fixture(session):
    # Injeta a sessão de teste fake dentro da dependência do FastAPI
    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_chat_com_tutor_inativo_deve_retornar_403(client, session):
    # 1. Cadastra um tutor fake inativo direto no banco de teste
    tutor_fake = Tutor(
        titulo="Tutor Desativado",
        status=False,  # INATIVO
        instrucoes_comportamento="Você é um bot inativo.",
        url_fonte_conhecimento="https://example.com",
    )
    session.add(tutor_fake)
    session.commit()
    session.refresh(tutor_fake)

    # 2. Dispara a requisição de chat para a rota pública
    payload = {
        "tutor_id": str(tutor_fake.id),  # Passando o UUID real
        "sessao_id": "teste-sessao-123",
        "mensagem": "Olá, tutor",
    }
    response = client.post("/api/v1/chat", json=payload)

    # 3. Asserção estrita do requisito não funcional (Erro 403 Forbidden)
    assert response.status_code == 403
    assert response.json()["detail"] == "Este tutor esta inativo no momento."
