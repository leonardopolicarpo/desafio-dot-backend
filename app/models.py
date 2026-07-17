"""
models.py
=========
Modelos de dados (SQLModel) da Plataforma de Tutores Personalizados.

Contém:
  - Tutor: entidade principal administrada via rotas /admin.
  - Mensagem: histórico de conversas por sessão, vinculado a um Tutor.
  - Schemas auxiliares (Create/Update/Read) para validação de entrada/saída
    da API, separando o modelo de tabela (persistência) dos modelos de I/O.
"""

import uuid
from datetime import datetime
from typing import Optional, Literal

from sqlmodel import SQLModel, Field


# ---------------------------------------------------------------------------
# TUTOR
# ---------------------------------------------------------------------------

class TutorBase(SQLModel):
  """Campos compartilhados entre criação, leitura e atualização de Tutor."""

  titulo: str = Field(
    index=True,
    max_length=255,
    nullable=False,
    description="Nome/título de exibição do tutor.",
  )
  status: bool = Field(
    default=True,
    nullable=False,
    description="Indica se o tutor está ativo (True) ou inativo (False).",
  )
  instrucoes_comportamento: str = Field(
    nullable=False,
    description="Prompt/instruções de sistema que definem a persona e o comportamento do tutor.",
  )
  url_fonte_conhecimento: str = Field(
    nullable=False,
    max_length=2048,
    description="URL da fonte de conhecimento (RAG) utilizada pelo tutor.",
  )


class Tutor(TutorBase, table=True):
  """Tabela persistida no banco de dados."""

  __tablename__ = "tutores"

  id: uuid.UUID = Field(
    default_factory=uuid.uuid4,
    primary_key=True,
    index=True,
    nullable=False,
  )
  criado_em: datetime = Field(default_factory=datetime.utcnow, nullable=False)
  atualizado_em: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class TutorCreate(TutorBase):
  """Payload para criação de um novo Tutor (POST)."""
  pass


class TutorUpdate(SQLModel):
  """
  Payload para atualização completa/parcial de um Tutor (PUT/PATCH).
  Todos os campos são opcionais para permitir atualizações parciais.
  """

  titulo: Optional[str] = None
  status: Optional[bool] = None
  instrucoes_comportamento: Optional[str] = None
  url_fonte_conhecimento: Optional[str] = None


class TutorRead(TutorBase):
  """Payload de resposta (GET) para um Tutor."""

  id: uuid.UUID
  criado_em: datetime
  atualizado_em: datetime


# ---------------------------------------------------------------------------
# MENSAGEM
# ---------------------------------------------------------------------------

RoleType = Literal["user", "assistant"]


class MensagemBase(SQLModel):
  """Campos compartilhados entre criação e leitura de Mensagem."""

  sessao_id: str = Field(
    index=True,
    max_length=255,
    nullable=False,
    description="Identificador da sessão de conversa (agrupa mensagens de um mesmo chat).",
  )
  tutor_id: uuid.UUID = Field(
    foreign_key="tutores.id",
    index=True,
    nullable=False,
    description="Referência ao Tutor responsável por esta mensagem.",
  )
  role: str = Field(
    max_length=20,
    nullable=False,
    description="Papel do emissor da mensagem: 'user' ou 'assistant'.",
  )
  content: str = Field(
    nullable=False,
    description="Conteúdo textual da mensagem.",
  )
  timestamp: datetime = Field(
    default_factory=datetime.utcnow,
    nullable=False,
    description="Data/hora de criação da mensagem (UTC).",
  )


class Mensagem(MensagemBase, table=True):
  """Tabela persistida no banco de dados."""

  __tablename__ = "mensagens"

  id: uuid.UUID = Field(
    default_factory=uuid.uuid4,
    primary_key=True,
    index=True,
    nullable=False,
  )


class MensagemCreate(MensagemBase):
  """Payload para criação de uma nova Mensagem."""
  pass


class MensagemRead(MensagemBase):
  """Payload de resposta (GET) para uma Mensagem."""

  id: uuid.UUID


# ---------------------------------------------------------------------------
# CHAT (rota pública /api/v1/chat)
# ---------------------------------------------------------------------------

class ChatRequest(SQLModel):
  """Payload de entrada da rota pública de chat."""

  tutor_id: uuid.UUID = Field(description="ID do Tutor com quem o usuário está conversando.")
  sessao_id: str = Field(
    min_length=1,
    max_length=255,
    description="Identificador da sessão de conversa (agrupa o histórico do chat).",
  )
  mensagem: str = Field(
    min_length=1,
    description="Texto da mensagem enviada pelo usuário.",
  )


class ChatResponse(SQLModel):
  """Payload de resposta da rota pública de chat."""

  tutor_id: uuid.UUID
  sessao_id: str
  resposta: str