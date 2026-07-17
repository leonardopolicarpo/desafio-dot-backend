"""
main.py
========
Ponto de entrada da API — Plataforma de Tutores Personalizados.

Responsabilidades deste módulo:
  1. Inicializar o banco de dados (SQLite) na subida da aplicação.
  2. Configurar CORS de forma flexível, permitindo que a API seja
    consumida por widgets embutidos em <iframe> hospedados em
    domínios variados (ex.: sites de clientes que embutem o chat).
  3. Ocultar stack traces em respostas HTTP 500, retornando uma
    mensagem genérica ao cliente e registrando o erro real apenas
    nos logs do servidor.
  4. Expor rotas administrativas (CRUD de Tutores) protegidas por um
    token estático enviado no header "X-Admin-API-Key".

Execução (desenvolvimento):
  uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Variáveis de ambiente relevantes:
  ADMIN_API_KEY           Token estático exigido nas rotas /admin/*.
                          OBRIGATÓRIO definir em produção.
  CORS_ALLOW_ORIGIN_REGEX Regex de origens permitidas para CORS.
                          Padrão: aceita qualquer origem https/http.
  DATABASE_FILE           Caminho do arquivo SQLite (ver database.py).
"""

import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.agent import executar_conversa
from app.database import create_db_and_tables, get_session
from app.models import (
    ChatRequest,
    ChatResponse,
    Mensagem,
    Tutor,
    TutorCreate,
    TutorRead,
    TutorUpdate,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Configuramos um logger dedicado para registrar os detalhes reais das
# exceções não tratadas. Essas informações NUNCA são enviadas ao cliente,
# apenas persistidas nos logs do servidor para fins de depuração/auditoria.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tutor_platform")


# ---------------------------------------------------------------------------
# Configurações sensíveis (via variáveis de ambiente)
# ---------------------------------------------------------------------------
# ATENÇÃO: em produção, defina ADMIN_API_KEY como uma variável de ambiente
# forte e secreta (ex.: `export ADMIN_API_KEY="$(openssl rand -hex 32)"`).
# O valor abaixo é apenas um fallback de desenvolvimento e é
# propositalmente inseguro para forçar a configuração explícita.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dev-only-change-me-in-producao")

if ADMIN_API_KEY == "dev-only-change-me-in-producao":
    logger.warning(
        "ADMIN_API_KEY não definida via ambiente. Usando valor de "
        "desenvolvimento inseguro. NÃO utilize esta configuração em produção."
    )

# Regex de origens permitidas para CORS. Por padrão, permite qualquer
# origem http(s) — necessário para suportar iframes embutidos em hosts
# variados e ainda não conhecidos previamente (multi-tenant/whitelabel).
# Em cenários que exigem maior restrição, defina esta variável de ambiente
# com uma regex específica, ex.: "^https://(.+\\.)?meudominio\\.com$".
CORS_ALLOW_ORIGIN_REGEX = os.getenv(
    "CORS_ALLOW_ORIGIN_REGEX",
    r"^https?://.*$",
)


# ---------------------------------------------------------------------------
# Ciclo de vida da aplicação
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria as tabelas do banco de dados na inicialização da aplicação."""
    create_db_and_tables()
    logger.info("Banco de dados inicializado e tabelas verificadas/criadas.")
    yield
    logger.info("Encerrando aplicação.")


# ---------------------------------------------------------------------------
# Instância da aplicação
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Plataforma de Tutores Personalizados — API",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Observações importantes sobre a configuração abaixo:
#   - allow_origin_regex é usado (em vez de allow_origins=["*"]) para que
#     seja possível, se necessário, combinar com allow_credentials=True
#     no futuro (o que não é permitido pela especificação CORS quando se
#     usa o coringa "*" literal).
#   - Hoje allow_credentials=False, pois a autenticação admin é feita via
#     header customizado (X-Admin-API-Key) e não via cookies de sessão,
#     eliminando a necessidade de credenciais de navegador entre origens.
#   - allow_headers inclui explicitamente o header de autenticação
#     customizado utilizado pelas rotas administrativas.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-API-Key", "Authorization"],
)


# ---------------------------------------------------------------------------
# Tratamento de erros — ocultar stack traces em respostas HTTP 500
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Captura qualquer exceção não tratada na aplicação (erros de
    programação, falhas de banco, etc.) e:
      1. Registra o stack trace completo nos logs do servidor
        (exc_info=True) para diagnóstico interno.
      2. Retorna ao cliente apenas uma mensagem genérica, sem detalhes
        internos de implementação, evitando vazamento de informação
        sensível (caminhos de arquivo, nomes de variáveis, versões de
        bibliotecas, queries SQL, etc.).
    """
    logger.error(
        "Erro não tratado em %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno no servidor. Tente novamente mais tarde."},
    )


# ---------------------------------------------------------------------------
# Autenticação administrativa
# ---------------------------------------------------------------------------
async def verificar_admin_api_key(
    x_admin_api_key: Optional[str] = Header(default=None, alias="X-Admin-API-Key"),
) -> None:
    """
    Dependency que protege as rotas administrativas.

    Valida a presença e a correção do header "X-Admin-API-Key" comparando
    seu valor ao token estático configurado em ADMIN_API_KEY, utilizando
    secrets.compare_digest para mitigar ataques de timing.
    """
    if not x_admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Header 'X-Admin-API-Key' ausente.",
        )

    if not secrets.compare_digest(x_admin_api_key, ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credencial administrativa inválida.",
        )


# ---------------------------------------------------------------------------
# Rotas públicas
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Infra"])
async def health_check() -> dict:
    """Endpoint simples de verificação de disponibilidade da API."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Rotas administrativas — CRUD de Tutores
# ---------------------------------------------------------------------------
admin_router_prefix = "/admin/tutores"


@app.post(
    admin_router_prefix,
    response_model=TutorRead,
    status_code=status.HTTP_201_CREATED,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def criar_tutor(
    tutor_in: TutorCreate,
    session: Session = Depends(get_session),
) -> Tutor:
    """Cria um novo Tutor."""
    tutor = Tutor.model_validate(tutor_in)
    session.add(tutor)
    session.commit()
    session.refresh(tutor)
    return tutor


@app.get(
    admin_router_prefix,
    response_model=List[TutorRead],
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def listar_tutores(
    status_filtro: Optional[bool] = Query(
        default=None,
        alias="status",
        description="Filtra por tutores ativos (true) ou inativos (false). Omitir retorna todos.",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> List[Tutor]:
    """Lista os Tutores cadastrados, com filtro opcional por status e paginação."""
    query = select(Tutor)
    if status_filtro is not None:
        query = query.where(Tutor.status == status_filtro)
    query = query.offset(offset).limit(limit)
    tutores = session.exec(query).all()
    return tutores


@app.get(
    admin_router_prefix + "/{tutor_id}",
    response_model=TutorRead,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def obter_tutor(
    tutor_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> Tutor:
    """Retorna os dados de um Tutor específico pelo seu ID."""
    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )
    return tutor


@app.put(
    admin_router_prefix + "/{tutor_id}",
    response_model=TutorRead,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def atualizar_tutor(
    tutor_id: uuid.UUID,
    tutor_in: TutorCreate,
    session: Session = Depends(get_session),
) -> Tutor:
    """
    Atualiza integralmente um Tutor existente (substituição completa dos
    campos editáveis: titulo, status, instrucoes_comportamento,
    url_fonte_conhecimento).
    """
    from datetime import datetime

    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )

    dados_atualizados = tutor_in.model_dump()
    for campo, valor in dados_atualizados.items():
        setattr(tutor, campo, valor)
    tutor.atualizado_em = datetime.utcnow()

    session.add(tutor)
    session.commit()
    session.refresh(tutor)
    return tutor


@app.patch(
    admin_router_prefix + "/{tutor_id}",
    response_model=TutorRead,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def atualizar_tutor_parcial(
    tutor_id: uuid.UUID,
    tutor_in: TutorUpdate,
    session: Session = Depends(get_session),
) -> Tutor:
    """
    Atualiza parcialmente um Tutor existente. Apenas os campos enviados
    no corpo da requisição são alterados.
    """
    from datetime import datetime

    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )

    dados_atualizados = tutor_in.model_dump(exclude_unset=True)
    for campo, valor in dados_atualizados.items():
        setattr(tutor, campo, valor)
    tutor.atualizado_em = datetime.utcnow()

    session.add(tutor)
    session.commit()
    session.refresh(tutor)
    return tutor


@app.patch(
    admin_router_prefix + "/{tutor_id}/desativar",
    response_model=TutorRead,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def desativar_tutor(
    tutor_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> Tutor:
    """Desativa um Tutor (define status=False) sem removê-lo do banco."""
    from datetime import datetime

    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )

    tutor.status = False
    tutor.atualizado_em = datetime.utcnow()

    session.add(tutor)
    session.commit()
    session.refresh(tutor)
    return tutor


@app.patch(
    admin_router_prefix + "/{tutor_id}/ativar",
    response_model=TutorRead,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def ativar_tutor(
    tutor_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> Tutor:
    """Reativa um Tutor previamente desativado (define status=True)."""
    from datetime import datetime

    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )

    tutor.status = True
    tutor.atualizado_em = datetime.utcnow()

    session.add(tutor)
    session.commit()
    session.refresh(tutor)
    return tutor


@app.delete(
    admin_router_prefix + "/{tutor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Admin - Tutores"],
    dependencies=[Depends(verificar_admin_api_key)],
)
async def excluir_tutor(
    tutor_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> None:
    """
    Remove definitivamente um Tutor do banco de dados.

    Nota: para a maioria dos fluxos administrativos, prefira o endpoint
    PATCH .../desativar (soft delete), preservando o histórico de
    mensagens vinculadas. Este endpoint executa exclusão física (hard
    delete) e deve ser usado com cautela.
    """
    tutor = session.get(Tutor, tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor não encontrado.",
        )

    session.delete(tutor)
    session.commit()
    return None


# ---------------------------------------------------------------------------
# Rota publica -- Chat com o Tutor (pipeline agentico via pydantic-ai)
# ---------------------------------------------------------------------------
JANELA_HISTORICO_MENSAGENS = 6


@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
    tags=["Chat"],
)
async def chat(
    chat_in: ChatRequest,
    session: Session = Depends(get_session),
) -> ChatResponse:
    """Rota publica de conversacao com um Tutor."""
    tutor = session.get(Tutor, chat_in.tutor_id)
    if tutor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tutor nao encontrado.",
        )

    if not tutor.status:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este tutor esta inativo no momento.",
        )

    query_historico = (
        select(Mensagem)
        .where(Mensagem.sessao_id == chat_in.sessao_id)
        .where(Mensagem.tutor_id == chat_in.tutor_id)
        .order_by(Mensagem.timestamp.desc())
        .limit(JANELA_HISTORICO_MENSAGENS)
    )
    historico_recente_desc = session.exec(query_historico).all()
    historico_cronologico = list(reversed(historico_recente_desc))

    resposta_ia = await executar_conversa(
        tutor=tutor,
        mensagem_usuario=chat_in.mensagem,
        historico_mensagens=historico_cronologico,
    )

    mensagem_usuario_db = Mensagem(
        sessao_id=chat_in.sessao_id,
        tutor_id=chat_in.tutor_id,
        role="user",
        content=chat_in.mensagem,
    )
    mensagem_ia_db = Mensagem(
        sessao_id=chat_in.sessao_id,
        tutor_id=chat_in.tutor_id,
        role="assistant",
        content=resposta_ia,
    )
    session.add(mensagem_usuario_db)
    session.add(mensagem_ia_db)
    session.commit()

    return ChatResponse(
        tutor_id=chat_in.tutor_id,
        sessao_id=chat_in.sessao_id,
        resposta=resposta_ia,
    )
