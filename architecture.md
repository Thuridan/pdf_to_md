# Arquitetura do sistema

Documento de design da arquitetura geral do `pdf_to_md`: como o motor de
conversão, a API web e a UI se encaixam, e as decisões de design por trás da
separação entre eles. Para o design de cada camada isoladamente, ver
[`code.md`](code.md) (motor/CLI), [`backend.md`](backend.md) (API) e
[`frontend.md`](frontend.md) (UI).

## Visão geral

O projeto tem uma regra central: **existe um único motor de conversão**
(`pdf_to_md.py`), e tudo o resto — CLI, API web, testes — é uma casca em
volta dele. Isso não é um detalhe de implementação incidental; é a decisão
de arquitetura mais importante do repositório, porque o motor Docling é caro
de instanciar (carrega layout model + TableFormer + OCR na memória) e lento
de reinstanciar. Qualquer camada nova adicionada ao projeto herda essa
restrição: ela precisa reaproveitar uma única instância do motor, nunca criar
uma por requisição/arquivo.

```
                    ┌─────────────────────────┐
                    │      pdf_to_md.py        │   motor de conversão
                    │  (Config, MotorBase,     │   + CLI (única fonte
                    │   MotorDocling/Simples,  │   de verdade)
                    │   converter_arquivo, …)  │
                    └────────────┬─────────────┘
                                 │ import direto (mesmo processo)
              ┌──────────────────┴──────────────────┐
              │                                      │
     ┌────────▼────────┐                   ┌─────────▼─────────┐
     │   CLI (argparse)  │                   │  backend/ (FastAPI) │
     │   modo lote /      │                  │  API + worker único  │
     │   interativo       │                  └─────────┬─────────┘
     └────────────────────┘                             │ StaticFiles mount
                                                ┌─────────▼─────────┐
                                                │  frontend/ (HTML/  │
                                                │  CSS/JS estático)   │
                                                └────────────────────┘
```

## Duas superfícies, um motor

| | CLI | Web app |
|---|---|---|
| Entrada | `-i` (arquivos/dirs) | upload multipart (`POST /api/jobs`) |
| Paralelismo | `--jobs N` (só motor `simples`) | fila sequencial, 1 worker |
| Motor | escolhido por `--engine` a cada execução | escolhido **uma vez** no startup do processo (`motor_pool`) |
| Progresso | log linha a linha | polling HTTP + estimativa por página |
| Reuso do motor | uma instância por *execução* do processo CLI | uma instância por *vida do processo* uvicorn |

A CLI já reaproveitava uma única instância de `DocumentConverter` para todo
um lote (dentro de uma execução). O backend estende essa mesma garantia para
a vida inteira do processo: `motor_pool.inicializar()` roda uma vez no
`lifespan` do FastAPI, e cada job subsequente chama `motor_pool.obter_motor()`
em vez de recriar o motor.

## Fluxo de uma conversão via web

1. **Upload** — `POST /api/jobs` recebe um ou mais arquivos. Cada PDF válido
   é gravado em `backend/uploads/{job_id}.pdf` e um `Job` é criado com status
   `na_fila`. A contagem de páginas é feita na hora (barata, via
   `pdf_to_md.contar_paginas`) para alimentar a barra de progresso depois.
2. **Enfileiramento** — o `job_id` entra numa `queue.Queue` em memória.
   `POST /api/jobs` retorna imediatamente; a conversão em si roda em
   background.
3. **Processamento** — a thread worker única (`jobs._loop`) tira um item da
   fila por vez e chama `pdf_to_md.converter_arquivo()` contra a instância
   compartilhada do motor. Enquanto processa, `job.status = "processando"`;
   o progresso reportado pela API é uma **estimativa** por tempo decorrido
   (o Docling não expõe callback nativo por página).
4. **Conclusão** — sucesso grava `{job_id}.md` ao lado do PDF (escrita
   atômica, herdada do motor) e atualiza uma média móvel global de
   segundos/página, usada para estimar os próximos jobs. Falha marca
   `status = "erro"` com a mensagem do motor.
5. **Entrega** — `GET /api/jobs/{id}/download` ou `GET /api/download-zip`
   servem o(s) `.md` do disco. Nenhum dado passa por banco — o disco *é* o
   armazenamento.
6. **Limpeza** — `DELETE /api/jobs/{id}` (ou `DELETE /api/jobs` em lote)
   remove o job do registro em memória e apaga o PDF de entrada + o `.md` de
   saída do disco. Sem isso, uploads se acumulariam indefinidamente durante
   a vida do processo.

A UI faz *polling* de `GET /api/jobs` a cada 1.5s e re-renderiza a fila
inteira a cada resposta — não há WebSocket nem Server-Sent Events. Ver
[`frontend.md`](frontend.md) para o porquê dessa escolha.

## Modelo de concorrência

- **Um processo** uvicorn por instância da aplicação (sem múltiplos workers
  `--workers N`, propositalmente: o motor de modelos vive na memória do
  processo e não pode ser compartilhado entre processos sem recarregá-lo em
  cada um).
- **Uma thread worker** consome a fila sequencialmente. Isso não é uma
  limitação temporária — é a mesma decisão que a CLI toma com `--jobs`
  (ignorado para o motor Docling): rodar duas conversões Docling em paralelo
  significaria duplicar os modelos em memória sem ganho real de throughput,
  já que o gargalo é a inferência, não a orquestração.
- **`JobStore`** é protegido por `threading.Lock`, pois é lido pelas rotas
  HTTP (thread do event loop / threadpool do Starlette) e escrito pela thread
  worker.
- **Sem fila externa** (Redis/Celery/RabbitMQ): a fila é um `queue.Queue` em
  memória porque o estado inteiro da aplicação já vive na memória de um único
  processo. Introduzir um broker externo resolveria um problema que não
  existe aqui (múltiplos workers) e criaria um que não existe hoje
  (persistência entre reinícios).

## Persistência (ou a ausência dela)

Não há banco de dados. O estado de um job é:

- **Em memória** (`JobStore`) — id, status, timestamps, progresso.
- **Em disco** (`backend/uploads/`) — o PDF de entrada e o `.md` de saída.

Reiniciar o processo (`restart.sh`) perde a fila e o histórico de jobs, mas
não perde nenhum arquivo já convertido enquanto ele não for removido do
disco manualmente. Essa é uma escolha deliberada de escopo: é uma ferramenta
de uso local/single-tenant, não um serviço multiusuário com necessidade de
durabilidade entre reinícios.

## Deploy e operação

```
start.sh     # sobe uvicorn em background (nohup), grava PID em .run/, espera /api/health
stop.sh      # mata o PID salvo, com fallback para SIGKILL se não parar
restart.sh   # stop.sh seguido de start.sh
```

- `HOST`/`PORT` configuráveis por variável de ambiente (padrão
  `127.0.0.1:8000` — não exposto externamente por padrão).
- PID e log ficam em `.run/` (gitignored) — não há supervisor de processo
  (systemd/supervisord); os scripts fazem esse papel de forma mínima.
- `backend/uploads/` e `.run/` são gitignored: são estado de execução, não
  código-fonte.
- Não há autenticação, HTTPS ou rate limiting na camada web — o design
  assume execução local/confiável (`127.0.0.1`), não exposição direta à
  internet.

## Testes como parte da arquitetura

Dois suites independentes espelham a separação motor/web:

- `test_pdf_to_md.py` (raiz) — 76 testes do motor/CLI, sem depender do
  FastAPI.
- `backend/tests/` — 52 testes da API, que **forçam `engine="simples"`** no
  `lifespan` via monkeypatch de `motor_pool.inicializar`. Isso é
  intencional: os testes de fila/worker precisam de um motor real (não um
  mock) para validar o ciclo de vida do `Job`, mas não podem depender do
  Docling pesado rodando em CI. `MotorSimples` cumpre esse papel por ser
  determinístico e não carregar modelos.

Essa divisão é o motivo pelo qual `backend/tests/test_jobs.py` também define
um `_MotorDeTeste` (stub que registra ordem de chamadas) para os testes mais
finos de ordenação/serialização da fila, independente de qual motor real
está instalado na máquina de CI.

## Decisões que não foram tomadas (e por quê)

- **Sem microsserviços** — motor, fila e API convivem no mesmo processo
  Python porque o motor não pode ser compartilhado entre processos sem
  recarregar modelos; separar em serviços exigiria resolver esse problema
  primeiro (ex.: um serviço de inferência dedicado), o que está fora do
  escopo de uma ferramenta local.
- **Sem build step no frontend** — ver [`frontend.md`](frontend.md).
- **Sem ORM/banco** — não há dado que precise sobreviver a um restart além
  dos próprios arquivos `.md`, que já são o produto final.
