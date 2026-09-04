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
restrição: ela precisa reaproveitar as instâncias do motor, nunca criar uma
por requisição/arquivo.

**Atualização (rodada 3, TAREFA-4): "uma única instância" virou "uma
instância por modo de OCR"**, não mais uma instância única e exclusiva. O
motivo é do Docling, não uma escolha nossa: `do_ocr` é campo de
`PdfPipelineOptions`, fixado na construção do `DocumentConverter` (não é
argumento de `convert()`), e `DocumentConverter._get_pipeline()` cacheia
pipelines internamente por hash (`md5` do `model_dump_json()`) das opções —
`do_ocr` diferente é, para o Docling, um pipeline diferente. Como a rodada 3
(TAREFA-3) passou a decidir OCR por job (detecção automática de camada de
texto, com override manual), `MotorDocling` precisa manter um
`DocumentConverter` por modo (`True`/`False`), não mais um único. Ambos
continuam preguiçosos — nenhum é criado no startup, só quando um job daquele
modo aparece pela primeira vez (ver `code.md`/`backend.md` para o código).

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
| Reuso do motor | uma instância *por modo de OCR* por execução do processo CLI | uma instância *por modo de OCR* pela vida do processo uvicorn |

A CLI já reaproveitava uma instância de `DocumentConverter` por modo de OCR
para todo um lote (dentro de uma execução) — na prática, quase sempre uma
só, porque a CLI decide OCR uma vez por execução (`--ocr`/`--no-ocr`), não
por arquivo. O backend estende essa mesma garantia para a vida inteira do
processo: `motor_pool.inicializar()` roda uma vez no `lifespan` do FastAPI, e
cada job subsequente chama `motor_pool.obter_motor()` em vez de recriar o
motor — mas como a rodada 3 decide OCR *por job* (TAREFA-3), o processo web
tende a acumular os dois modos ao longo do tempo, não só um.

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
  worker — mas o lock protege o **dicionário** (`adicionar`/`obter`/
  `remover`/`listar`/`remover_se_nao_processando`), não os campos de um
  `Job` individual. `_processar()` muta `job.status`, `job.caminho_saida`
  etc. diretamente, fora de qualquer lock. Isso não corrompe nada porque em
  CPython a atribuição de um único atributo já é atômica (GIL), mas é uma
  garantia mais fraca do que "toda leitura/escrita passa pelo lock" —
  concretamente, significa que um leitor pode observar um `Job` em qualquer
  ponto intermediário entre duas atribuições seguidas. Por isso a ordem de
  atribuição dentro de `_processar()` importa (`status` é sempre a última
  coisa setada, depois do dado que ele implica existir) e por isso
  `remover_se_nao_processando()` existe como método dedicado do `JobStore`
  em vez de um check-then-act em duas chamadas separadas.
- **Sem fila externa** (Redis/Celery/RabbitMQ): a fila é um `queue.Queue` em
  memória porque o estado inteiro da aplicação já vive na memória de um único
  processo. Introduzir um broker externo resolveria um problema que não
  existe aqui (múltiplos workers) e criaria um que não existe hoje
  (persistência entre reinícios).

## Custo de memória por modo de OCR e por documento (rodada 3, TAREFA-4)

Medido no `.venv` de desenvolvimento (Python 3.12.3, `docling` 2.123.1,
Ubuntu 24.04.4, 62 GiB de RAM), RSS real do processo uvicorn via
`/proc/<pid>/status`, servidor real rodando (não um mock):

| Cenário | RSS |
|---|---|
| (1) Nenhum converter carregado (logo após o startup) | ~51 MB |
| (2) Só o converter **com** OCR carregado | ~1,49 GB |
| (3) Só o converter **sem** OCR carregado | ~1,41 GB |
| (4) Os dois carregados (mesmo processo) | ~1,88 GB |

O item (4) é bem menor que a soma ingênua de (2)+(3) (~2,9 GB) — os dois
`DocumentConverter`s compartilham bastante memória de baixo nível (runtime
do PyTorch, bibliotecas, cache de alocador), então manter os dois modos
vivos no mesmo processo custa bem menos do que dois processos separados,
um por modo, custariam. Dado relevante para uma futura decisão de pool de
processos — a rodada 3 não implementa paralelismo, só mede.

**Durante a conversão de um documento real** (não sintético — 20 e 50
páginas extraídas do miolo do manual de 1310 páginas usado para calibrar a
detecção de OCR na TAREFA-3, motor `simples` fora, Docling com OCR
desligado): o RSS **não** cresce de forma discreta por página como a tabela
acima sugere — ele explode para uma faixa de dezenas de GB assim que a
conversão começa, dominada por um custo que já aparece quase todo com só 20
páginas, não crescendo muito mais até 50:

| Páginas convertidas | RSS no pico durante a conversão | RSS após concluir |
|---|---|---|
| 20 | ~17,7 GB | ~14,0 GB |
| 50 | ~19,6 GB | ~15,1 GB |

O RSS não volta ao patamar de antes da conversão (~1,9 GB) depois de
concluída — fica na casa de 14-15 GB mesmo com o job já `"concluido"`. Não
foi investigado se isso é o `DoclingDocument`/estruturas internas do
Docling genuinely retidas, ou só o comportamento normal de alocadores de
memória (glibc/PyTorch) que não devolvem páginas ociosas ao SO — de
qualquer forma, é RSS real que o processo mantém.

**Divergência registrada:** a tarefa pede para medir "durante a conversão
de um documento grande" — a intenção evidente é o documento de 1310 páginas
usado para calibrar a TAREFA-3. Extrapolando os dois pontos acima (que já
sugerem um custo dominado por um patamar alto e não-linear, não um
crescimento pequeno e constante por página), processar as 1310 páginas
inteiras seria uma aposta arriscada demais para rodar sem supervisão nesta
máquina compartilhada de 62 GiB — o risco de um OOM real (derrubando outros
processos da máquina, não só o teste) supera o valor do dado adicional.
**Não foi executado.** Os dois pontos (20 e 50 páginas) já estabelecem o
fato mais importante para uma decisão futura de pool de processos: o custo
por conversão real do Docling é dominado por um patamar de dezenas de GB
que aparece cedo, não por um crescimento pequeno e previsível por página —
qualquer dimensionamento de paralelismo baseado em "X MB por página" seria
otimista demais com os números desta tabela.

### O patamar cresce a cada job no mesmo processo? (rodada 6, TAREFA-2)

**A medição mais importante da rodada — decide o desenho de qualquer pool
de processos futuro.** Sabia-se que o RSS fica em um patamar alto depois de
um job e não volta ao nível anterior à conversão; não se sabia se esse
patamar **estabiliza** (retenção de alocador — paliativo resolve) ou
**cresce** a cada job (objeto vivo acumulando — paliativo não resolve).

**Método:** pelo caminho do backend (`motor_pool.inicializar()` uma vez,
depois `motor.converter()` reaproveitado — o caminho real do servidor de
vida longa, não uma CLI nova por job), 4 documentos em sequência no mesmo
processo, RSS amostrado por uma thread (`psutil`, a cada 0,2s), separado por
modo de OCR.

**Sem OCR** (4 faixas de 30 páginas, `teste.pdf`, cada uma cobrindo uma
região diferente do documento):

| Job | RSS antes | RSS no pico | RSS depois |
|---|---|---|---|
| 1 | 17 MB | 16.551 MB | 12.363 MB |
| 2 | 12.363 MB | 27.395 MB | 21.188 MB |
| 3 | 21.188 MB | 32.614 MB | 27.575 MB |
| 4 | 27.575 MB | 35.974 MB | 32.280 MB |

**Com OCR** (4 faixas de 10 páginas — reduzidas de 30 para 10 nesta rodada
depois que uma faixa de 30 páginas com OCR passou de 51 GB e continuava
subindo quando foi abortada manualmente por segurança, ver ressalva
abaixo):

| Job | RSS antes | RSS no pico | RSS depois |
|---|---|---|---|
| 1 | 17 MB | 20.393 MB | 12.175 MB |
| 2 | 12.175 MB | 26.345 MB | 21.077 MB |
| 3 | 21.077 MB | 32.213 MB | 25.422 MB |
| 4 | 25.422 MB | 38.105 MB | 29.417 MB |

**Resultado: o patamar CRESCE a cada job, nos dois modos.** Não estabiliza
em 3-4 jobs — o "depois" de cada job fica sistematicamente mais alto que o
"depois" do job anterior (crescimento de +9-10 GB no primeiro salto,
diminuindo mas não zerando nos seguintes — ~+4 a +5 GB do job 3 para o 4).
Isso aponta para acumulação de objeto vivo entre conversões (algum cache do
Docling, ou referências seguradas por module-level state), não só
retenção de alocador — a hipótese que motivou o Bloco B (TAREFA-5) a testar
paliativos, e o motivo pelo qual eles reduzem mas **não eliminam** o
crescimento (ver TAREFA-5, abaixo).

**Ressalva de segurança, registrada:** durante a calibração, uma faixa de
30 páginas com OCR de uma região específica do documento (páginas 150-179)
sozinha ultrapassou 51 GB de RSS e continuava subindo quando foi
interrompida manualmente (`kill -9`) para proteger a máquina de 62 GiB —
bem acima do patamar de ~44 GB já registrado na rodada 4 para 46 páginas.
Essa é mais uma confirmação de que **o pico é dominado por conteúdo, não
por contagem de páginas** (já registrado na rodada 3): essa faixa específica
tem algo (provavelmente uma imagem de página inteira em alta resolução ou
diagrama denso) que a torna atipicamente cara. As faixas de 10 páginas
usadas na tabela "Com OCR" acima foram escolhidas depois dessa observação,
justamente para não repetir o mesmo risco.

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
scripts/start.sh     # sobe uvicorn em background (nohup), grava PID em .run/, espera /api/health
scripts/stop.sh      # mata o PID salvo, com fallback para SIGKILL se não parar
scripts/restart.sh   # stop.sh seguido de start.sh
```

- `HOST`/`PORT` configuráveis por variável de ambiente (padrão `0.0.0.0:8000`
  — escuta em todas as interfaces, incluindo a rede local; use
  `HOST=127.0.0.1` para restringir a esta máquina). `scripts/start.sh`
  imprime um aviso visível em stderr sempre que `HOST` resolver para algo
  além de loopback, nomeando a exposição — silencioso só com
  `127.0.0.1`/`localhost`/`::1`.
- PID e log ficam em `.run/` (gitignored) — não há supervisor de processo
  (systemd/supervisord); os scripts fazem esse papel de forma mínima.
- `backend/uploads/` e `.run/` são gitignored: são estado de execução, não
  código-fonte.
- Não há autenticação, HTTPS ou rate limiting na camada web — o design
  assume execução em rede local/confiável, controlada pelo firewall do
  sistema (`ufw`), não exposição direta à internet.

## Testes como parte da arquitetura

Dois suites independentes espelham a separação motor/web:

- `test_pdf_to_md.py` (raiz) — testes do motor/CLI, sem depender do
  FastAPI.
- `backend/tests/` — testes da API, que **forçam `engine="simples"`** no
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
  escopo de uma ferramenta local. Ver "`docling-serve` como motor
  remoto" abaixo — é exatamente esse "serviço de inferência dedicado",
  avaliado e não adotado nesta rodada.
- **Sem build step no frontend** — ver [`frontend.md`](frontend.md).
- **Sem ORM/banco** — não há dado que precise sobreviver a um restart além
  dos próprios arquivos `.md`, que já são o produto final.

### `docling-serve` como motor remoto (avaliado, rodada 5, TAREFA-7)

**Investigação e registro — nada foi instalado ou migrado.** `docling-serve`
não está instalado neste ambiente; a investigação usou o
`docling.service_client` (já embutido no pacote `docling` 2.123.1
instalado) e o código-fonte público de `docling-serve`/`docling-jobkit`
(GitHub, via `gh api`, lido diretamente — não presumido).

**1. Cobertura das opções que o projeto usa hoje.** Verificado no
`ConvertDocumentsOptions` (`docling.datamodel.service.options`, parte do
pacote `docling` local, usado pelo `DoclingServiceClient`):

| Opção que o projeto usa | Campo do `ConvertDocumentsOptions` | Cobertura |
|---|---|---|
| `--ocr/--no-ocr` (`cfg.ocr`) | `do_ocr: bool` | completa |
| `--lang` (`cfg.lang`) | `ocr_lang: list[str] \| None` | completa |
| `--tables` (`cfg.tables`) | `table_mode: TableFormerMode` + `table_cell_matching: bool` | completa |
| Seleção de páginas (rodada futura) | `page_range: Tuple[int, int]` — parâmetro de primeira classe em `client.convert()` | completa, já pronta |
| Marcador de página (rodada 5, TAREFA-1) | `md_page_break_placeholder: str` | completa |
| `--timeout` (`cfg.timeout`) | `document_timeout: float \| None` | completa |
| `--ocr-backend` (RapidOCR: onnxruntime/openvino/paddle/torch) | sem campo dedicado — só `ocr_custom_config: dict` (opaco, não tipado) | **incerta**, não testável sem instância real |
| `--device`/`--threads` (`cfg.device`, `cfg.threads`) | nenhum campo | **não coberta** — vira configuração do servidor (`DOCLING_SERVE_ENG_LOC_*`), não do cliente |
| Supressão de ícones (rodada 5, TAREFA-2) | nenhum campo REST | **não coberta pela API** — mas não perdida: `client.convert()` devolve o mesmo `docling.datamodel.document.ConversionResult` da conversão local (confirmado lendo `docling/service_client/client.py` — mesma classe importada), então `resultado.document` continua sendo um `DoclingDocument` de verdade. `_exportar_markdown()`/`_numerar_paginas()` (deste projeto) funcionariam sem alteração sobre um resultado remoto. |
| Grau de confiança (rodada 5, TAREFA-3) | `resultado.confidence` — mesma classe, mas **não verificado** se o servidor de fato preenche e serializa esse campo na resposta (não há instância real para testar) |

Ou seja: um hipotético `MotorDoclingServe(MotorBase)` poderia reaproveitar
`_exportar_markdown()`, `_numerar_paginas()` e `_extrair_confianca()` sem
mudança nenhuma — eles operam sobre `resultado.document`/`.confidence`,
que têm o mesmo formato local ou remoto. Só a chamada que produz
`resultado` mudaria (`DoclingServiceClient.convert()` em vez de
`DocumentConverter.convert()`). O que se perde ao migrar: controle fino de
acelerador por processo (`--device`/`--threads`) e o backend específico do
RapidOCR — ambos viram decisão do operador do servidor, não do chamador.

**2. O que `SHARE_MODELS` realmente compartilha — mais limitado do que a
descrição do prompt desta rodada sugeria.** Lido em
`docling_jobkit/orchestrators/local/worker.py`
(`docling-project/docling-jobkit`, GitHub): com `ENG_LOC_SHARE_MODELS=true`,
os `ENG_LOC_NUM_WORKERS` workers **dentro do mesmo processo** (tarefas
`asyncio`, não processos separados) compartilham uma única instância de
`DoclingConverterManager` — o mesmo conjunto de modelos carregados
(layout + TableFormer + OCR). Com o padrão (`false`), cada worker constrói
o seu próprio `DoclingConverterManager` — N workers, N cópias completas dos
modelos, no mesmo processo.

**Isso não é a mesma coisa que os ~22 GB por worker que inviabilizaram o
pool de processos** (round de gestão de memória, ainda não feita) — nas
medições reais deste projeto (rodada 3, TAREFA-4), um `DocumentConverter`
carregado custa **~1,4–1,9 GB** de RSS por modo de OCR, não ~22 GB. O custo
de dezenas de GB medido nas rodadas 3/4 (18–44 GB de pico) vem do
**processamento de página em si** (bitmaps, ativações do layout/TableFormer,
OCR por região) durante uma conversão ativa — não do carregamento estático
dos modelos. `SHARE_MODELS` deduplica só a parte estática (o modelo
carregado uma vez); dois workers convertendo documentos grandes **ao mesmo
tempo**, mesmo com `SHARE_MODELS=true`, ainda pagam o pico de RSS de
processamento de página **cada um**, porque esse custo não está nos pesos
do modelo — está no trabalho em andamento. `SHARE_MODELS` ajuda o caso "N
workers ociosos ou processando documentos pequenos"; não resolve o caso
que motivou a rodada de memória futura (documentos grandes, processados em
paralelo).

**3. O que fica de fora, mesmo usando `docling-serve`:** isolamento por
usuário (não há conceito de dono de job — resultados expiram por
`result_removal_delay`, sem ACL), a UI (é só API + um cliente Python, sem
frontend de fila como o deste projeto), fila persistente/histórico de jobs
(orientado a tarefa única com expiração, não a um registro duradouro como
`JobStore`), e seleção de páginas na UI (o campo `page_range` existe na
API, mas nada no `docling-serve` desenha um seletor).

**4. Custo operacional:** um processo/container a mais para subir, versionar
e monitorar — hoje o projeto é um único processo Python. Migrar exigiria
decidir hospedagem (self-hosted vs. um serviço gerenciado como o
mencionado na doc do cliente) e adicionar uma dependência de rede onde
antes havia uma chamada de função local.

**Decisão:** não tomada aqui, por instrução explícita — este registro é
material de entrada para o usuário decidir depois, com base em dados
verificados (não nas descrições da origem da rodada) e considerando que a
motivação original (`SHARE_MODELS` resolveria o custo de memória) só se
sustenta parcialmente.
