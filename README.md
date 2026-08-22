# MatVerse SGSI Stack

**MatVerse** é um organismo digital autônomo que implementa um sistema soberano de governança (SGSI - Sistema de Governança e Soberania da Inteligência) com memória geométrica auditável (MNB - Minimal Auditable Blocks), ledger encadeado e integração com APIs externas.

## Arquitetura

O sistema é composto por cinco camadas acopladas:

1. **Memória Geométrica (MNB)**: Armazena blocos mínimos auditáveis com embeddings, âncoras geométricas e métricas de persistência.
2. **Ledger Encadeado**: Trilha de auditoria imutável com hashing SHA-256 e encadeamento por hash anterior.
3. **SGSI (Sistema de Governança)**: Motor de decisão autônomo que calcula métricas (Ψ, Θ, CVaR, PoLE, Ω) e toma decisões (PASS, ESCALATE, BLOCK).
4. **API FastAPI**: Endpoints RESTful para processamento de consultas, gerenciamento de memória e acesso ao ledger.
5. **UI Gradio**: Interface web interativa para testes e demonstração do sistema.

## Componentes Principais

### `config.py`
Centraliza todas as configurações de ambiente, caminhos de dados e variáveis de aplicação.

### `memory.py`
Define o modelo **MNB** (Minimal Auditable Block) e a classe **GeometricMemory** para gerenciar memória com busca por similaridade cosseno.

### `ledger.py`
Implementa o **Ledger** com entradas encadeadas por hash SHA-256, garantindo imutabilidade e auditabilidade.

### `sgsi.py`
Contém as classes de governança: **AlgorithmBody** (métricas), **GovernanceBody** (decisões), **SkillBody** (mutações) e **SGSI** (orquestração).

### `upstream.py`
Wrapper para integração com APIs externas (STACK_API) com fallback local e normalização de respostas.

### `service.py`
Orquestra as interações entre memória, ledger, SGSI e API externa.

### `api.py`
Define endpoints FastAPI para acesso aos serviços.

### `main.py`
Assembla a aplicação FastAPI com UI Gradio integrada.

## Endpoints da API

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/health` | Status de saúde do sistema |
| POST | `/api/process` | Processa consulta com SGSI e memória |
| POST | `/api/memory/add` | Adiciona novo MNB à memória |
| POST | `/api/memory/search` | Busca por similaridade na memória |
| GET | `/api/mnb/{mnb_id}` | Recupera MNB específico |
| GET | `/api/ledger` | Retorna todas as entradas do ledger |

## Instalação Local

### Pré-requisitos
- Python 3.11+
- pip ou conda

### Passos

```bash
# Clonar ou extrair o projeto
cd matverse_stack_production

# Instalar dependências
pip install -r requirements.txt

# Executar o servidor
python -m uvicorn src.matverse_stack.main:app --host 0.0.0.0 --port 7860
```

O servidor estará disponível em `http://localhost:7860`.

## Uso via cURL

### Health Check
```bash
curl http://localhost:7860/api/health
```

### Processar Consulta
```bash
curl -X POST http://localhost:7860/api/process \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Teste MatVerse",
    "top_k": 3,
    "add_to_memory": true,
    "metadata": {"source": "curl"}
  }'
```

### Adicionar MNB
```bash
curl -X POST http://localhost:7860/api/memory/add \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Novo bloco de memória",
    "source": "api",
    "metadata": {"kind": "test"}
  }'
```

### Buscar Memória
```bash
curl -X POST http://localhost:7860/api/memory/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MatVerse",
    "top_k": 5
  }'
```

### Visualizar Ledger
```bash
curl http://localhost:7860/api/ledger
```

## Variáveis de Ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `STACK_API_URL` | (vazio) | URL da API externa (STACK_API) |
| `STACK_API_KEY` | (vazio) | Chave de autenticação para STACK_API |
| `STACK_API_TIMEOUT_SEC` | 20 | Timeout para requisições à STACK_API |
| `HOST` | 0.0.0.0 | Host do servidor |
| `PORT` | 7860 | Porta do servidor |
| `SGSI_AGENT_NAME` | MatVerse-Agent | Nome do agente SGSI |
| `SGSI_AGENT_ORCID` | (vazio) | ORCID do agente |
| `APP_API_KEY` | (vazio) | Chave de API para endpoints mutáveis |

## Docker

### Build da Imagem
```bash
docker build -t matverse-sgsi:latest .
```

### Executar Container
```bash
docker run -p 7860:7860 \
  -e STACK_API_URL="https://sua-api.com/process" \
  -e STACK_API_KEY="sua-chave" \
  -v /data:/app/data \
  matverse-sgsi:latest
```

## Métricas do SGSI

O sistema calcula as seguintes métricas:

- **Ψ (Psi)**: Coerência do sistema (0-1)
- **Θ (Theta)**: Encarnação/Embodiment (0-1)
- **CVaR**: Conditional Value at Risk (0-1) - métrica de risco
- **PoLE**: Proof of Liveness (0-1)
- **Ω (Omega)**: Fitness geral = 0.4Ψ + 0.3Θ + 0.2(1-CVaR) + 0.1PoLE

### Decisões de Governança

- **PASS**: CVaR ≤ 0.05 E Ψ ≥ 0.85 E Ω ≥ 0.85
- **BLOCK**: CVaR > 0.05 (veto duro)
- **ESCALATE**: Caso contrário

## Estrutura de Dados

### MNB (Minimal Auditable Block)
```json
{
  "mnb_id": "uuid",
  "content": "texto do bloco",
  "content_hash": "hash SHA-256",
  "embedding": [array de floats],
  "geometric_anchor": [array de floats],
  "psi": 1.0,
  "epsilon": 0.0,
  "kappa": 0.0,
  "persistence": 1.0,
  "source": "origem",
  "metadata": {}
}
```

### Ledger Entry
```json
{
  "timestamp": 1234567890.0,
  "event_type": "query_processed",
  "payload": {},
  "prev_hash": "hash anterior",
  "entry_hash": "hash desta entrada"
}
```

## Persistência de Dados

- **memory.json**: Armazena todos os MNBs em JSON
- **ledger.jsonl**: Armazena entradas do ledger em JSONL (uma entrada por linha)

Ambos os arquivos são salvos em `/data` (configurável via `DATA_DIR`).

## Segurança

- Endpoints mutáveis (`/api/memory/add`) requerem header `X-API-Key` se `APP_API_KEY` estiver configurada
- Ledger é imutável e auditável
- Hashing SHA-256 garante integridade das entradas

## Próximos Passos

1. **Embeddings Reais**: Substituir `embed_text()` por um encoder real (SentenceTransformers, OpenAI, etc.)
2. **Persistência em Banco de Dados**: Migrar de JSON/JSONL para PostgreSQL ou SQLite com WAL
3. **Assinatura Digital**: Adicionar assinatura de entradas do ledger
4. **Replicação**: Implementar replicação do ledger entre nós
5. **Governança Distribuída**: Estender SGSI para decisões multi-agente

## Licença

CC-BY-4.0

## Autor

Mateus Alves Arêas (ORCID: 0009-0008-2973-4047)

## Referências

- SGSI: Sistema de Governança e Soberania da Inteligência
- MNB: Minimal Auditable Blocks para LLMs
- MatVerse: Organismo Digital Autônomo
