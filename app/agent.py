"""
agent.py
=========
Pipeline de conversação do Tutor usando a biblioteca `pydantic-ai`.

Estratégia agêntica (SEM embeddings / SEM banco vetorial):
  'O agente possui uma única tool nativa, `buscar_conhecimento`, que faz
  um GET HTTP simples e assíncrono (via `httpx`) na URL cadastrada em
  `Tutor.url_fonte_conhecimento` e devolve o texto bruto da página para
  compor o contexto do LLM. Não há indexação, chunking ou similaridade
  vetorial — o "RAG" aqui é deliberadamente ingênuo, por especificação
  do proje'to (MVP).

Resiliência:
  A tool aplica um timeout estrito de 5 segundos. Qualquer falha de
  rede, timeout ou status HTTP de erro é capturada e convertida em uma
  string amigável, instruindo o próprio agente a responder apenas com
  base nas `instrucoes_comportamento` (persona) do tutor, sem expor
  detalhes técnicos do erro ao usuário final.

Configuração do modelo:
  O modelo LLM utilizado é definido pela variável de ambiente
  AGENT_MODEL (padrão: "openai:gpt-4o-mini"). O prefixo antes dos dois
  pontos identifica o provider para o pydantic-ai (ex.: "openai:",
  "anthropic:", "google-gla:"). Configure a API key correspondente via
  variável de ambiente do próprio provider (ex.: OPENAI_API_KEY,
  ANTHROPIC_API_KEY, GEMINI_API_KEY) — o pydantic-ai lê essas variáveis
  automaticamente, não é necessário passá-las explicitamente no código.
"""

import logging
import os
from dataclasses import dataclass
from typing import List

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
  ModelMessage,
  ModelRequest,
  ModelResponse,
  TextPart,
  UserPromptPart,
)

from app.models import Mensagem, Tutor

logger = logging.getLogger("tutor_platform.agent")

# ---------------------------------------------------------------------------
# Configuração do modelo
# ---------------------------------------------------------------------------
AGENT_MODEL_NAME = os.getenv("AGENT_MODEL", "openai:gpt-4o-mini")

# Timeout estrito (em segundos) para a busca da fonte de conhecimento
# externa dentro da tool `buscar_conhecimento`, conforme especificação.
HTTP_FETCH_TIMEOUT_SECONDS = 5.0

# Limite de caracteres do conteúdo bruto devolvido pela tool, para evitar
# estourar a janela de contexto do modelo com páginas muito grandes.
MAX_CONTEUDO_FONTE_CHARS = 6000


# ---------------------------------------------------------------------------
# Dependências injetadas no agente
# ---------------------------------------------------------------------------
@dataclass
class TutorAgentDeps:
  """
  Dependências (deps) disponibilizadas ao agente e às suas tools durante
  a execução, via `RunContext.deps`.
  """

  tutor: Tutor


# ---------------------------------------------------------------------------
# Definição do Agent
# ---------------------------------------------------------------------------
tutor_agent: Agent[TutorAgentDeps, str] = Agent(
  AGENT_MODEL_NAME,
  deps_type=TutorAgentDeps,
  output_type=str,
  retries=1,
)


@tutor_agent.instructions
def montar_instrucoes_de_persona(ctx: RunContext[TutorAgentDeps]) -> str:
  """
  Instrução dinâmica (system prompt) montada em tempo de execução a
  partir da persona/comportamento configurada para o Tutor associado à
  sessão atual.
  """
  tutor = ctx.deps.tutor
  return (
    f"Você é o tutor '{tutor.titulo}'. Siga estritamente as instruções "
    f"de comportamento e persona abaixo, definidas pelo administrador "
    f"da plataforma:\n\n"
    f"---\n{tutor.instrucoes_comportamento}\n---\n\n"
    "Quando a pergunta do usuário exigir informação factual específica "
    "sobre o material/curso deste tutor, utilize a tool "
    "'buscar_conhecimento' para consultar a fonte de conhecimento "
    "cadastrada antes de responder. Se a tool informar que a fonte "
    "externa está indisponível, responda mesmo assim, apoiando-se "
    "apenas nestas instruções de persona, sem mencionar detalhes "
    "técnicos do erro ao usuário — apenas informe, de forma breve e "
    "gentil, que o material de apoio não pôde ser consultado agora."
  )


@tutor_agent.tool
async def buscar_conhecimento(ctx: RunContext[TutorAgentDeps]) -> str:
  """Busca o conteúdo bruto da fonte de conhecimento oficial deste tutor.

  Use esta ferramenta sempre que precisar de informação factual,
  específica ou atualizada sobre o conteúdo/curso deste tutor antes de
  responder ao usuário. Não invente informações que deveriam vir da
  fonte de conhecimento.
  """
  url = ctx.deps.tutor.url_fonte_conhecimento

  if not url:
    return (
      "Nenhuma fonte de conhecimento externa está configurada para "
      "este tutor. Responda utilizando apenas as instruções de "
      "persona base, sem tentar buscar conteúdo externo."
    )

  try:
    async with httpx.AsyncClient(timeout=HTTP_FETCH_TIMEOUT_SECONDS) as client:
      resposta = await client.get(url)
      resposta.raise_for_status()
      conteudo = resposta.text

    if len(conteudo) > MAX_CONTEUDO_FONTE_CHARS:
      conteudo = conteudo[:MAX_CONTEUDO_FONTE_CHARS] + "\n[...conteúdo truncado...]"

    return conteudo

  except httpx.TimeoutException:
    logger.warning("Timeout (%ss) ao buscar fonte de conhecimento: %s", HTTP_FETCH_TIMEOUT_SECONDS, url)
    return (
      "A fonte de conhecimento externa não respondeu a tempo "
      "(timeout). Responda ao usuário utilizando apenas as suas "
      "instruções de comportamento/persona base, informando de "
      "forma breve e gentil que o material de apoio está "
      "temporariamente indisponível, sem citar detalhes técnicos."
    )
  except httpx.HTTPStatusError as exc:
    logger.warning(
      "Fonte de conhecimento retornou status de erro (%s) para: %s",
      exc.response.status_code,
      url,
    )
    return (
      "A fonte de conhecimento externa retornou um erro e não pôde "
      "ser lida. Responda ao usuário utilizando apenas as suas "
      "instruções de comportamento/persona base, informando de "
      "forma breve e gentil que o material de apoio está "
      "temporariamente indisponível, sem citar detalhes técnicos."
    )
  except httpx.RequestError as exc:
    logger.warning("Falha de rede ao buscar fonte de conhecimento (%s): %s", type(exc).__name__, url)
    return (
      "Não foi possível conectar à fonte de conhecimento externa. "
      "Responda ao usuário utilizando apenas as suas instruções de "
      "comportamento/persona base, informando de forma breve e "
      "gentil que o material de apoio está temporariamente "
      "indisponível, sem citar detalhes técnicos."
    )


# ---------------------------------------------------------------------------
# Conversão do histórico persistido (Mensagem/SQLModel) para o formato de
# message_history esperado pelo pydantic-ai.
# ---------------------------------------------------------------------------
def montar_historico_pydantic_ai(mensagens: List[Mensagem]) -> List[ModelMessage]:
  """
  Converte uma lista de `Mensagem` — já ordenada cronologicamente da
  mais antiga para a mais recente — no formato `message_history`
  aceito pelo `Agent.run` do pydantic-ai.

  Mensagens com role="user" viram `ModelRequest(UserPromptPart(...))`.
  Mensagens com role="assistant" viram `ModelResponse(TextPart(...))`.
  Roles desconhecidos são ignorados silenciosamente para não quebrar a
  execução do agente por dado legado/inconsistente.
  """
  historico: List[ModelMessage] = []
  for msg in mensagens:
    if msg.role == "user":
      historico.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
    elif msg.role == "assistant":
      historico.append(ModelResponse(parts=[TextPart(content=msg.content)]))
  return historico


# ---------------------------------------------------------------------------
# Orquestração de alto nível: executa uma rodada de conversa
# ---------------------------------------------------------------------------
async def executar_conversa(
  tutor: Tutor,
  mensagem_usuario: str,
  historico_mensagens: List[Mensagem],
) -> str:
  """
  Executa uma rodada do agente para o `tutor` informado, usando
  `historico_mensagens` (janela deslizante já recuperada do banco,
  ordenada da mais antiga para a mais recente) como contexto prévio, e
  retorna o texto de resposta gerado pela IA.
  """
  deps = TutorAgentDeps(tutor=tutor)
  message_history = montar_historico_pydantic_ai(historico_mensagens)

  resultado = await tutor_agent.run(
    mensagem_usuario,
    deps=deps,
    message_history=message_history,
  )
  return resultado.output