# Plataforma de Tutores Personalizados — Backend (MVP)

API backend (FastAPI + SQLModel + SQLite) responsável pela administração de Tutores e pelo pipeline de conversação agêntico (Pydantic AI) consumido por widgets de chat embutidos via `<iframe>`.

---

## 📋 Sumário

1. [Declaração de Desenvolvimento Assistido por IA](#-declaração-de-desenvolvimento-assistido-por-ia-requisito-7f)
2. [Decisões de Arquitetura e Justificativas de Trade-off](#-decisões-de-arquitetura-e-justificativas-de-trade-off-requisitos-43a-44a-e-5a)
3. [Diagrama de Arquitetura](#-diagrama-de-arquitetura-requisito-8a)
4. [Documentação das Rotas e Variáveis de Ambiente](#-documentação-das-rotas-e-variáveis-de-ambiente-requisitos-41b-43c-e-5d)
5. [Próximos Passos para Produção](#-próximos-passos-para-produção-requisito-8b--sem-implementar)
6. [Setup Rápido](#️-setup-rápido)

---

## 🤖 DECLARAÇÃO DE DESENVOLVIMENTO ASSISTIDO POR IA (Requisito 7.f)

Este projeto foi desenvolvido em um fluxo de **engenharia assistida por Inteligência Artificial**, utilizando o modelo **Claude (Anthropic)** como ferramenta de aceleração para a geração inicial de boilerplate, estruturação de rotas, modelagem de dados e integração da biblioteca `pydantic-ai`.

Declaramos formalmente que:

- Todo o código gerado por IA foi produzido **sob supervisão humana direta e contínua**, com prompts técnicos estritos definindo stack, contratos de dados, requisitos de segurança e critérios de aceite.
- A **arquitetura, as decisões de trade-off, a validação funcional (testes manuais e automatizados dos endpoints) e a auditoria de segurança** (CORS, ocultação de stack traces, autenticação administrativa) foram conduzidas e aprovadas por um responsável técnico humano antes da integração ao repositório.
- Nenhum código foi incorporado ao projeto sem revisão crítica prévia; trechos gerados pela IA foram tratados como **sugestões de primeira versão**, sujeitas a correção, refino e validação equivalente à de qualquer contribuição de um engenheiro humano.
- O uso de IA neste fluxo tem finalidade exclusiva de **aceleração de desenvolvimento de um MVP**, não substituindo processos de revisão de código, testes de segurança ou validação de conformidade que antecedem a promoção deste software para ambiente de produção.

Esta declaração cobre a totalidade dos artefatos de código-fonte presentes neste diretório (`/back`) até a data da última atualização deste README.

---

## 🧠 DECISÕES DE ARQUITETURA E JUSTIFICATIVAS DE TRADE-OFF (Requisitos 4.3.a, 4.4.a e 5.a)

### Justificativa de Tecnologia de IA: Pydantic AI vs. LangChain

Optamos estritamente por **Pydantic AI** em vez de LangChain (ou frameworks agênticos similares) pelos seguintes motivos:

- **Tipagem estrita nativa**: Pydantic AI é construído sobre o mesmo motor de validação do Pydantic v2, já utilizado em todo o restante da stack (FastAPI + SQLModel). Isso significa que `deps_type`, `output_type` e os parâmetros de cada `tool` são validados em tempo de execução com o mesmo contrato de tipos do resto da aplicação, sem exigir uma segunda camada de abstração ou um "schema paralelo" para descrever ferramentas ao LLM.
- **Menor sobrecarga de abstrações**: o LangChain introduz camadas adicionais (Chains, Runnables, Callbacks, Memory abstractions) que, para o escopo deste MVP — um único agente com uma tool e uma janela de histórico simples — representariam complexidade acidental sem benefício correspondente. Pydantic AI expõe uma API mais direta (`Agent`, `@agent.tool`, `RunContext`), reduzindo a curva de aprendizado e a superfície de bugs.
- **Validação nativa em tempo de execução**: falhas de validação de saída do LLM (quando aplicável) disparam automaticamente um novo ciclo de retry dentro do próprio framework, sem necessidade de lógica de parsing manual ou try/except ad-hoc espalhado pelo código de negócio.
- **Alinhamento com o restante do stack**: como o projeto já usa SQLModel (que por sua vez é construído sobre Pydantic), adotar Pydantic AI mantém uma única "linguagem de tipos" fim a fim — do banco de dados até a camada de IA — o que reduz a carga cognitiva de manutenção.

**Trade-off assumido**: Pydantic AI é um framework mais jovem e com um ecossistema de integrações prontas (loaders de documentos, conectores de bancos vetoriais, etc.) menor que o do LangChain. Para este MVP, isso é aceitável porque a estratégia de conhecimento é deliberadamente simples (busca HTTP direta, sem RAG vetorial — ver seção de tools). Caso o produto evolua para múltiplos agentes orquestrados, pipelines complexos de RAG ou integrações prontas com dezenas de fontes de dados, essa decisão deve ser reavaliada.

### Justificativa de Persistência: SQLite

O uso de **SQLite** como banco de dados foi uma escolha deliberada para a fase de MVP, e não uma limitação técnica:

- **Banco local em arquivo**: elimina a necessidade de provisionar, configurar e manter um serviço de banco de dados externo (PostgreSQL, MySQL, etc.) apenas para validar o produto.
- **Zero latência de rede**: por rodar no mesmo processo/host da aplicação, as consultas de histórico de conversa (janela deslizante) e de CRUD de tutores não sofrem overhead de round-trip de rede, o que é especialmente relevante na rota de chat, onde cada requisição já paga o custo de latência de uma chamada a um LLM externo.
- **Zero infraestrutura adicional**: não exige Docker, orquestração de containers ou serviços gerenciados de nuvem — um requisito explícito para permitir que o MVP seja demonstrado e testado localmente (inclusive no ambiente de desenvolvimento em Arch Linux + Neovim usado neste projeto) sem fricção operacional.
- **Coerência com a escala do MVP**: o volume de escrita (mensagens de chat, tutores cadastrados) e o número de conexões concorrentes esperados nesta fase são baixos o suficiente para que as limitações conhecidas do SQLite (lock de escrita único por arquivo, ausência de concorrência real multi-processo) não representem um risco prático.

**Trade-off assumido**: SQLite não é adequado para alta concorrência de escrita nem para múltiplas instâncias da aplicação escalando horizontalmente atrás de um load balancer (cada instância teria seu próprio arquivo `.db`, gerando inconsistência de dados). Esta limitação é conhecida e endereçada na seção *Próximos Passos para Produção* abaixo.

### Justificativa de Segurança e CORS: Regex vs. Wildcard

A configuração de CORS deste projeto usa `allow_origin_regex` (uma expressão regular) em vez do coringa literal `allow_origins=["*"]`, por duas razões técnicas concretas:

1. **Compatibilidade futura com credenciais**: a especificação CORS proíbe o uso simultâneo de `allow_origins=["*"]` com `allow_credentials=True`. Como o widget é embutido via `<iframe>` em domínios de terceiros e o produto pode evoluir para exigir autenticação de sessão baseada em cookie (em vez de apenas o header estático `X-Admin-API-Key`), usar uma regex desde já mantém a porta aberta para essa evolução sem exigir uma reescrita da camada de CORS.
2. **Segurança coerente com o cenário de multi-embedding controlado**: mesmo aceitando múltiplas origens (necessário porque cada cliente que embute o widget em seu próprio site tem um domínio diferente e não previsível de antemão), a regex é a unidade de configuração que permite, a qualquer momento, restringir a superfície de origens aceitas — por exemplo, trocando o padrão permissivo padrão (`^https?://.*$`) por uma regex que aceite apenas subdomínios de um domínio conhecido (ex.: `^https://(.+\.)?meudominio\.com$`) — sem alterar a lógica da aplicação, apenas a variável de ambiente `CORS_ALLOW_ORIGIN_REGEX`. Isso transforma uma decisão que, com o wildcard `"*"`, ficaria "gravada em pedra" no código, em um parâmetro de configuração auditável e ajustável por ambiente (dev/staging/produção).

**Trade-off assumido**: no valor padrão atual (`^https?://.*$`), o comportamento efetivo ainda é permissivo — qualquer origem HTTP(S) é aceita — porque, na fase de MVP, os domínios finais que embutirão o widget ainda não são conhecidos. A regex resolve o problema de *design* (arquitetura pronta para restrição), mas a restrição *operacional* de fato (lista de domínios permitidos) é uma tarefa de configuração de ambiente que deve ser feita antes do lançamento em produção, definindo `CORS_ALLOW_ORIGIN_REGEX` com o conjunto real de domínios-cliente.

---

## 🏗️ DIAGRAMA DE ARQUITETURA (Requisito 8.a)

```
┌──────────────┐
│   Usuário     │
│  (navegador)  │
└──────┬────────┘
       │ interage com o widget de chat
       ▼
┌──────────────────────────────┐
│   Iframe Frontend             │
│   (widget.html, hospedado     │
│   em domínio de terceiros)    │
└──────┬────────────────────────┘
       │ POST /api/v1/chat
       │ { tutor_id, sessao_id, mensagem }
       │ (requisição cross-origin, liberada via CORS)
       ▼
┌───────────────────────────────────────────────────┐
│              FastAPI — Backend (/back)              │
│                                                     │
│   ┌─────────────────────────────────────────────┐  │
│   │ Rota POST /api/v1/chat                        │  │
│   │  1. Carrega Tutor (id, status, persona)       │  │
│   │  2. Consulta SQLite: últimas 6 mensagens        │  │
│   │     da sessao_id (janela deslizante)          │  │
│   └───────────────────┬────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│   ┌─────────────────────────────────────────────┐  │
│   │  Instanciação do Agente (Pydantic AI)          │  │
│   │  - deps: Tutor (persona/instruções)            │  │
│   │  - message_history: janela de 6 mensagens      │  │
│   │  - nova mensagem do usuário                    │  │
│   └───────────────────┬────────────────────────────┘  │
│                       │                              │
│                       │  o agente decide invocar       │
│                       │  a tool quando necessário       │
│                       ▼                              │
│   ┌─────────────────────────────────────────────┐  │
│   │  Tool assíncrona: buscar_conhecimento()        │  │
│   │  - GET via httpx.AsyncClient                   │  │
│   │  - timeout estrito de 5 segundos                │  │
│   └───────────────────┬────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│           ┌───────────────────────────┐              │
│           │  URL Fonte de Conhecimento  │              │
│           │  (texto bruto, sem index-   │              │
│           │  ação/embeddings)           │              │
│           └─────────────┬─────────────┘              │
│                       │                              │
│              sucesso ◄─┴─► timeout / erro              │
│                 │              │                      │
│                 ▼              ▼                      │
│         texto bruto    string amigável instruindo     │
│         devolvido ao   o agente a responder apenas     │
│         LLM como       com a persona base              │
│         contexto                                       │
│                       │                              │
│                       ▼                              │
│   ┌─────────────────────────────────────────────┐  │
│   │  Resposta final do LLM                         │  │
│   │  → persistida no SQLite (pergunta + resposta)  │  │
│   │  → devolvida em JSON simples ao iframe          │  │
│   └─────────────────────────────────────────────┘  │
│                                                     │
│   ┌─────────────────────────────────────────────┐  │
│   │ Rotas Admin (/admin/tutores/*)                 │  │
│   │  Protegidas por header X-Admin-API-Key         │  │
│   │  CRUD completo de Tutores                       │  │
│   └─────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │  SQLite (data/)   │
              │  tutores.db       │
              │  - tutores         │
              │  - mensagens       │
              └─────────────────┘
```

---

## 🔌 DOCUMENTAÇÃO DAS ROTAS E VARIÁVEIS DE AMBIENTE (Requisitos 4.1.b, 4.3.c e 5.d)

### Variáveis de Ambiente

| Variável                   | Obrigatória | Padrão                          | Descrição                                                                                                     |
|----------------------------|:-----------:|----------------------------------|-----------------------------------------------------------------------------------------------------------------|
| `ADMIN_API_KEY`            | ✅ (produção) | `dev-only-change-me-in-producao` | Token estático exigido no header `X-Admin-API-Key` para acessar qualquer rota `/admin/*`. Gere com `openssl rand -hex 32`. |
| `AGENT_MODEL`              | ❌           | `openai:gpt-4o-mini`             | Identificador do modelo LLM usado pelo Pydantic AI. O prefixo (`openai:`, `anthropic:`, `google-gla:`, etc.) define o provider. |
| `CORS_ALLOW_ORIGIN_REGEX`  | ❌           | `^https?://.*$`                  | Regex de origens permitidas para CORS (ver seção de justificativa de segurança acima).                          |
| `DATABASE_FILE`            | ❌           | `data/tutores.db`                | Caminho do arquivo SQLite. A pasta `data/` é criada automaticamente na raiz do projeto caso não exista.         |

Além disso, é necessário exportar a API key do provider de LLM escolhido em `AGENT_MODEL` (o Pydantic AI lê essas variáveis automaticamente, não é preciso passá-las no código):

| Provider (prefixo em `AGENT_MODEL`) | Variável de ambiente da API key |
|--------------------------------------|----------------------------------|
| `openai:`                            | `OPENAI_API_KEY`                 |
| `anthropic:`                         | `ANTHROPIC_API_KEY`              |
| `google-gla:`                        | `GEMINI_API_KEY`                 |

### Rotas Administrativas (`/admin/tutores`)

Todas exigem o header `X-Admin-API-Key`.

**Criar um Tutor (`POST /admin/tutores`):**

```bash
curl -X POST http://localhost:8000/admin/tutores \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI" \
  -H "Content-Type: application/json" \
  -d '{
        "titulo": "Tutor de Matemática",
        "status": true,
        "instrucoes_comportamento": "Seja paciente, didático e use exemplos do cotidiano.",
        "url_fonte_conhecimento": "https://exemplo.com/apostila-matematica.txt"
      }'
```

**Listar Tutores (`GET /admin/tutores`):**

```bash
curl -X GET "http://localhost:8000/admin/tutores?status=true&offset=0&limit=100" \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI"
```

**Obter um Tutor específico (`GET /admin/tutores/{tutor_id}`):**

```bash
curl -X GET http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI"
```

**Atualizar integralmente um Tutor (`PUT /admin/tutores/{tutor_id}`):**

```bash
curl -X PUT http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI" \
  -H "Content-Type: application/json" \
  -d '{
        "titulo": "Tutor de Matemática Avançada",
        "status": true,
        "instrucoes_comportamento": "Foque em cálculo diferencial e integral.",
        "url_fonte_conhecimento": "https://exemplo.com/apostila-calculo.txt"
      }'
```

**Atualizar parcialmente um Tutor (`PATCH /admin/tutores/{tutor_id}`):**

```bash
curl -X PATCH http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI" \
  -H "Content-Type: application/json" \
  -d '{"titulo": "Novo título apenas"}'
```

**Desativar um Tutor (`PATCH /admin/tutores/{tutor_id}/desativar`):**

```bash
curl -X PATCH http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI/desativar \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI"
```

**Reativar um Tutor (`PATCH /admin/tutores/{tutor_id}/ativar`):**

```bash
curl -X PATCH http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI/ativar \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI"
```

**Excluir definitivamente um Tutor (`DELETE /admin/tutores/{tutor_id}`):**

```bash
curl -X DELETE http://localhost:8000/admin/tutores/SEU_TUTOR_ID_AQUI \
  -H "X-Admin-API-Key: SEU_TOKEN_ADMIN_AQUI"
```

### Rota Pública de Conversação (`POST /api/v1/chat`)

Não exige autenticação (rota pública, consumida diretamente pelo widget embutido em iframe).

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
        "tutor_id": "SEU_TUTOR_ID_AQUI",
        "sessao_id": "sessao-do-usuario-123",
        "mensagem": "Pode me explicar o teorema de Pitágoras?"
      }'
```

Resposta esperada (`200 OK`):

```json
{
  "tutor_id": "SEU_TUTOR_ID_AQUI",
  "sessao_id": "sessao-do-usuario-123",
  "resposta": "Claro! O teorema de Pitágoras afirma que..."
}
```

Códigos de erro relevantes desta rota:

| Código | Situação                                             |
|--------|-------------------------------------------------------|
| `404`  | `tutor_id` não corresponde a nenhum Tutor cadastrado.  |
| `403`  | Tutor encontrado, porém está com `status=false` (inativo). |
| `422`  | Corpo da requisição inválido (ex.: `mensagem` vazia, `tutor_id` não é um UUID válido). |
| `500`  | Erro interno (ex.: falha inesperada na chamada ao provider de LLM); resposta genérica, sem stack trace exposto ao cliente. |

---

## 🛠️ Qualidade, Padronização e Testes (Requisito 5.c)

Para garantir a manutenibilidade, resiliência e conformidade com as boas práticas de engenharia do ecossistema Python moderno, o repositório possui ferramentas de análise estática e testes automatizados integrados de forma nativa ao fluxo do gerenciador de pacotes `uv`:

- **Linter e Formatador (Ruff):** Utilizamos o `ruff` para impor padronização estilística (PEP 8) e mitigar bugs em tempo de desenvolvimento.
- **Testes Automatizados (Pytest):** Implementação de testes de integração na rota crítica de conversação (`/api/v1/chat`), utilizando um banco SQLite em memória isolado para simular o comportamento de regras de negócio (como bloqueio de tutores inativos e injeção de histórico).

### Comandos de Execução:
- **Auditar qualidade e sintaxe:** `uv run ruff check app/`
- **Aplicar formatação automática:** `uv run ruff format app/`
- **Executar a suíte de testes:** `uv run pytest tests/`

---

## 🚀 PRÓXIMOS PASSOS PARA PRODUÇÃO (Requisito 8.b — SEM IMPLEMENTAR)

Os itens abaixo são um mapeamento técnico de evolução do produto para além do escopo deste MVP. **Nenhum deles está implementado neste repositório** — são registrados aqui como plano de ação para a próxima fase.

1. **Migração do SQLite para PostgreSQL em contêiner Docker**
  Necessária para suportar alta concorrência de escrita/leitura, múltiplas instâncias da aplicação escalando horizontalmente atrás de um load balancer, backups gerenciados e replicação. Envolve: (a) trocar a `DATABASE_URL` do SQLModel para o dialeto `postgresql+psycopg`; (b) provisionar um contêiner PostgreSQL (via `docker-compose`) com volume persistente; (c) introduzir uma ferramenta de migração de schema (ex.: Alembic) para versionar alterações no banco.

2. **Cache de TTL (via Redis) na Tool de fetch HTTP**
  Hoje, a tool `buscar_conhecimento` executa um `GET` HTTP a cada chamada do agente, mesmo que a `url_fonte_conhecimento` não tenha mudado entre requisições. Em produção, isso deve ser substituído por uma camada de cache com TTL configurável (ex.: 5–15 minutos) apoiada em Redis, chaveada pela própria URL do tutor: a tool consultaria o cache antes de disparar o `GET`, reduzindo latência (elimina o round-trip de rede na maioria das chamadas), reduzindo carga sobre a fonte de conhecimento externa, e absorvendo picos de tráfego sem multiplicar requisições para a mesma URL.

3. **Camada de assinatura/criptografia para o `tutor_id` do iframe (JWT de curta duração ou AES-256)**
  Atualmente, o `tutor_id` trafega em texto plano no corpo da requisição feita pelo widget, o que expõe a plataforma a spoofing (um cliente malicioso poderia, em tese, testar UUIDs de tutores de terceiros). A evolução recomendada é: o backend emitir, no momento em que o iframe é montado (ex.: em uma rota de "bootstrap" do widget), um token assinado — um JWT de curta duração (poucos minutos) contendo o `tutor_id` como claim, ou alternativamente um payload cifrado com AES-256 — que a rota `/api/v1/chat` passaria a exigir e validar em vez de aceitar o `tutor_id` bruto do cliente. Isso blinda a plataforma contra enumeração/spoofing de tutores e amarra cada sessão de chat a um tutor autorizado especificamente para aquele embed.

---

## 🛠️ Setup Rápido

```bash
cd back
uv sync
export ADMIN_API_KEY="$(openssl rand -hex 32)"
export OPENAI_API_KEY="sua-chave-aqui"   # ou ANTHROPIC_API_KEY / GEMINI_API_KEY, conforme AGENT_MODEL
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

A documentação interativa (Swagger UI) fica disponível em `http://localhost:8000/docs` após a subida do servidor.