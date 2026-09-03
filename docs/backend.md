# Backend — design da API web

Design detalhado de `backend/`: a API FastAPI construída sobre
`pdf_to_md.py`. Para como essa camada se encaixa no sistema como um todo, ver
[`architecture.md`](architecture.md); para o motor de conversão em si, ver
[`code.md`](code.md).

## Estrutura de pastas

```
backend/
├── __init__.py
├── src/
│   ├── app.py                  # cria o FastAPI, lifespan, monta rotas + estático
│   ├── routes/
│   │   └── api.py              # camada HTTP: valida requests, chama services/
│   └── services/
│       ├── jobs.py             # Job, JobStore, fila, worker, progresso, limpeza
│       └── motor_pool.py       # instância do motor por processo (uma por modo de OCR - ver abaixo)
└── tests/
    ├── test_app.py             # testes via TestClient (rotas)
    ├── test_jobs.py            # testes unitários do serviço de jobs
    └── test_motor_pool.py      # testes do singleton de motor
```

Deliberadamente **não existe** `models/` (sem banco — estado de jobs vive em
memória, ver [`architecture.md`](architecture.md#persistência-ou-a-ausência-dela))
nem `middlewares/` (nenhum necessário ainda: sem auth, sem CORS customizado).
`routes/api.py` é o "controller"; `services/` concentra toda a lógica de
negócio e reaproveita as mesmas funções que a CLI usa
(`converter_arquivo`, `selecionar_motor`, `contar_paginas`).

## `app.py` — bootstrap da aplicação

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    motor_pool.inicializar()   # escolhe o motor UMA vez por processo
    jobs.iniciar_worker()      # sobe a thread worker única
    try:
        yield
    finally:
        jobs.parar_worker()    # sinaliza fim + join com timeout

app = FastAPI(title="pdf_to_md", version=pdf_to_md.__version__, lifespan=lifespan)
app.include_router(router)
app.mount("/", StaticFiles(directory=DIR_FRONTEND, html=True), name="static")
```

Duas decisões relevantes aqui:

- **Ordem de registro importa.** `app.include_router(router)` roda antes do
  `app.mount("/", ...)`. FastAPI resolve rotas na ordem em que foram
  adicionadas, então `/api/*` sempre vence o mount estático — sem essa
  ordem, o `StaticFiles` (que serve `index.html` para qualquer path por
  causa de `html=True`) engoliria as chamadas de API. Isso é verificado
  explicitamente por `test_mount_estatico_nao_esconde_as_rotas_de_api`.
- **`motor_pool.inicializar()` não força carga eager dos modelos.** Ela só
  decide *qual* motor usar (`selecionar_motor`); o Docling continua
  carregando os modelos pesados sob demanda na primeira conversão real
  (dentro de `MotorDocling._obter_converter`). Isso mantém o startup do
  servidor rápido mesmo com o motor Docling selecionado.

## Um `DocumentConverter` por modo de OCR (rodada 3, TAREFA-4)

Antes da rodada 3, `MotorDocling` guardava um único `DocumentConverter` em
`self._conv`, construído sob demanda e reaproveitado para sempre — a regra
de "uma instância por processo" que `architecture.md` documentava. Isso
quebrou quando a TAREFA-3 passou a decidir OCR por job: `do_ocr` é campo de
`PdfPipelineOptions`, fixado na construção do `DocumentConverter` (não é
argumento de `convert()`), então **ligar e desligar OCR por job exige um
converter por modo** — cada um com sua própria cópia de layout e TableFormer
carregada (verificado no fonte do `docling` 2.123.1: `do_ocr` participa do
hash `md5` que `DocumentConverter._get_pipeline()` usa para cachear
pipelines internamente).

```python
class MotorDocling(MotorBase):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._convs: dict[bool, DocumentConverter] = {}  # chave: modo de OCR

    def _obter_converter(self, ocr: bool):
        if ocr not in self._convs:
            self._convs[ocr] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self._opcoes_pipeline(ocr)
                )}
            )
        return self._convs[ocr]

    def converter(self, pdf: Path, *, ocr: bool | None = None) -> str:
        efetivo = self.cfg.ocr if ocr is None else ocr
        resultado = self._obter_converter(efetivo).convert(str(pdf))
        ...
```

Ambos os converters continuam preguiçosos — nenhum é criado no startup
(`motor_pool.inicializar()` continua só decidindo qual motor, não carregando
modelo algum); cada um só é construído quando um job daquele modo aparece
pela primeira vez.

**A peça que faltava:** ter um `self._convs` por modo não bastava sozinho —
`converter_arquivo(pdf, saida, motor, cfg)` já recebia um `cfg` por
chamada, mas `motor.converter(pdf)` nunca o repassava; o motor sempre usava
`self.cfg.ocr`, fixado na construção. `_processar()` (TAREFA-3) já montava
um `Config` por job via `dataclasses.replace(cfg_global, ocr=job.ocr)`, mas
esse valor nunca chegava ao motor — o override não tinha efeito nenhum na
prática (nem "primeiro job vence": literalmente nenhum). A correção foi em
duas pontas: `MotorBase.converter()` ganhou um parâmetro `ocr: bool | None`
(explícito sobrepõe, `None` cai para `self.cfg.ocr` — compatível com todo
código existente que chama `.converter(pdf)` sem esse argumento, CLI
incluída), e `converter_arquivo()` passou a chamar
`motor.converter(pdf, ocr=cfg.ocr)` explicitamente.

**Custo de memória:** ver a nova seção "Custo de memória por modo de OCR e
por documento" em [`architecture.md`](architecture.md) — os dois modos
juntos custam bem menos que a soma ingênua dos dois isolados (memória
compartilhada de baixo nível), mas o custo por conversão de um documento
real domina qualquer coisa relacionada a quantos modos estão carregados.

## Rotas (`routes/api.py`)

| Método | Rota | Descrição | Códigos |
|---|---|---|---|
| `GET` | `/api/health` | Liveness check + versão do pacote. | `200` |
| `GET` | `/api/motor` | Qual motor está ativo neste processo (`docling`/`simples`). | `200` |
| `POST` | `/api/jobs` | Upload de um ou mais PDFs (`multipart/form-data`, campo `files`, mais `modo_ocr` opcional); enfileira cada um válido. | `200` (sempre — rejeições vêm no corpo) |
| `GET` | `/api/jobs` | Lista todos os jobs com status/progresso estimado. | `200` |
| `GET` | `/api/jobs/{id}` | Status de um job específico. | `200` / `404` |
| `DELETE` | `/api/jobs/{id}` | Remove um job e seus arquivos em disco. | `200` / `404` / `409` (processando) |
| `DELETE` | `/api/jobs` | Remove todos os jobs `concluido`/`erro` (e arquivos) — "Limpar finalizados". | `200` |
| `GET` | `/api/jobs/{id}/download` | Baixa o `.md` de um job concluído. | `200` / `404` / `409` (não concluído) |
| `GET` | `/api/download-zip?ids=` | Zip em memória com os `.md` dos concluídos (todos, ou filtrado por `ids` separados por vírgula). | `200` / `404` (nada para baixar) |

### `POST /api/jobs` — contrato de upload

```python
async def criar_jobs(
    files: list[UploadFile] = File(...),
    modo_ocr: str = Form("automatico"),
) -> dict:
    ...
    return {"criados": [...], "rejeitados": [...]}
```

Cada arquivo é classificado por extensão (`pdf_to_md.SUFIXOS_PDF`), não por
`Content-Type` — um upload malformado ou renomeado é pego pela extensão, não
confiando no header enviado pelo cliente. Arquivos não-PDF vão para
`rejeitados` com o motivo; o restante é gravado em disco (em blocos, direto
do `UploadFile` — ver `_gravar_upload_com_teto`) e enfileirado. A resposta é
sempre `200`: a rota nunca falha por causa de um arquivo ruim no meio de um
lote misto (ver `test_lote_misto_separa_criados_de_rejeitados`). Cada
arquivo do `criados` já vem com `estimativa_segundos`/
`estimativa_baixa_confianca` (rodada 3, `Job.to_dict()`) e `ocr`/
`ocr_origem` (ver abaixo).

### OCR automático por documento (rodada 3, TAREFA-3)

`modo_ocr` ("automatico"/"sempre"/"nunca", padrão "automatico" — um valor
desconhecido também cai para "automatico" em vez de rejeitar a requisição)
se aplica ao lote inteiro do upload. Em "automatico", `jobs._decidir_ocr()`
chama `pdf_to_md.tem_camada_de_texto(caminho_pdf)` por arquivo; a decisão
efetiva (`Job.ocr`) e sua origem (`Job.ocr_origem`: `"detectado"` ou
`"forcado"`) ficam gravadas no `Job` na criação — auditável depois, quando o
resultado sai pior que o esperado. Na dúvida (PDF ilegível pelo pypdfium2),
o padrão é rodar OCR: um falso negativo (OCR num PDF nativo) só desperdiça
tempo, um falso positivo (pular OCR num digitalizado) perde conteúdo.

Detecção em `tem_camada_de_texto()`: amostra até 12 páginas distribuídas ao
longo do documento (não as primeiras — capa/sumário não representam o
miolo) e decide pela *proporção* de páginas amostradas com conteúdo
substantivo (≥ 40 caracteres), não por "qualquer página com texto" — isso
tolera páginas legitimamente sem texto num nativo (diagramas, separadores)
sem empurrar a decisão para "digitalizado". Limiar de maioria (≥ 50%)
calibrado contra dois documentos reais (não sintéticos): um manual nativo
de 1310 páginas com texto rico em toda página amostrada, e um documento
digitalizado real de 26 páginas com zero texto extraível em todas elas. Não
havia um terceiro documento misto orgânico disponível para calibração — o
caso misto foi validado combinando páginas reais dos dois anteriores
(60% nativo + apêndice digitalizado real: detectado como nativo, OCR
desligado para o documento inteiro — o modo de falha conhecido documentado
no `README.md`).

O override por job só teve efeito completo depois da TAREFA-4 (mesma
rodada, item seguinte): até ali, `MotorDocling` cacheava UM
`DocumentConverter` por processo com `do_ocr` fixado nas opções do PRIMEIRO
job processado, e o `Config` por job que `_processar()` já montava nunca
chegava ao motor (`motor.converter(pdf)` não recebia `cfg` nenhum). Ver
"Um `DocumentConverter` por modo de OCR" mais abaixo para a correção
completa (as duas pontas: um converter por modo, e `cfg.ocr` de fato
repassado a `motor.converter()`).

### Convenção de erros

- **`404`** — recurso (job) não existe no `JobStore`.
- **`409`** — recurso existe, mas está no estado errado para a operação
  pedida (baixar um job ainda não concluído; remover um job em
  processamento). O corpo do erro (`detail`) sempre inclui o estado atual
  quando é útil para o cliente decidir o que fazer.

Essas duas rotas (`baixar_job`, `remover_job`) seguem o mesmo padrão: buscar
o job direto do `JobStore` na própria rota, checar o status ali, e só então
delegar a operação de fato ao módulo `jobs`. Isso mantém a lógica de
validação HTTP (o que vira 404 vs 409) na camada HTTP, e a lógica de efeito
colateral (apagar arquivo, mover status) na camada de serviço.

## `services/jobs.py` — registro e fila

### O dataclass `Job`

```python
@dataclass
class Job:
    id: str
    nome_original: str
    caminho_pdf: Path
    status: str = "na_fila"        # na_fila | processando | concluido | erro
    criado_em: datetime = ...
    mensagem_erro: str = ""
    caminho_saida: Path | None = None
    paginas_totais: int | None = None
    tamanho_bytes: int = 0
    iniciado_em: datetime | None = None
    segundos: float = 0.0
```

`Job.to_dict()` é onde a **estimativa de progresso** é calculada, não em um
campo armazenado — ela é derivada sob demanda a partir de `iniciado_em` e de
uma constante global `_segundos_por_pagina` (ver abaixo), para que o valor
retornado reflita o tempo decorrido no exato momento da requisição, não o
tempo em que o worker atualizou o job pela última vez.

### `JobStore` — thread safety

```python
class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
```

Um dicionário simples protegido por `Lock`, sem TTL nem paginação — é
adequado porque o volume esperado é "os jobs de uma sessão local", não
milhares de registros concorrentes. É lido pelas rotas HTTP (thread pool do
Starlette) e escrito pela thread worker; toda leitura/escrita passa pelo
lock.

### Fila e worker único

```python
_fila: queue.Queue[object] = queue.Queue()
_worker_thread: threading.Thread | None = None

def _loop():
    while True:
        item = _fila.get()
        if item is _SENTINEL:
            return
        _processar(item)
        _fila.task_done()
```

`enfileirar()` faz `queue.put()`, que nunca bloqueia — a rota `POST
/api/jobs` responde imediatamente, e o processamento real acontece na
thread `pdf-to-md-worker`. `iniciar_worker()`/`parar_worker()` são
idempotentes e ligados ao `lifespan` do FastAPI: o shutdown do servidor
sinaliza a thread com um sentinel e dá `join(timeout=5.0)`, evitando que o
processo trave esperando um job que nunca vai terminar.

Por que **uma** thread e não um pool: o gargalo é a instância compartilhada
do motor Docling, não a orquestração em Python. Rodar duas conversões
Docling "em paralelo" na mesma instância não é seguro (o `DocumentConverter`
não foi desenhado para chamadas concorrentes) e, com instâncias separadas,
duplicaria os modelos em memória sem ganho real de throughput — a mesma
lógica por trás do `--jobs` ser ignorado para o motor `docling` na CLI (ver
[`code.md`](code.md#paralelismo-de-lote)).

### Estimativa de progresso (EMA)

```python
_SEGUNDOS_POR_PAGINA_PADRAO = 2.0
_ALPHA_EMA = 0.3

def _atualizar_estimativa(job: Job) -> None:
    if not job.paginas_totais:
        return
    observado = job.segundos / job.paginas_totais
    _segundos_por_pagina = _ALPHA_EMA * observado + (1 - _ALPHA_EMA) * _segundos_por_pagina
```

`_processar()` só chama isso quando `resultado.status == "ok"` — um job
`"pulado"` (saída já existia, ver `converter_arquivo`) também conta como
concluído do ponto de vista do usuário, mas `resultado.segundos` nesse caso
é `0.0`; alimentar isso na média móvel faria a estimativa de progresso
convergir para "instantâneo", o que é falso para as conversões reais.

**Estimativa de duração total** (`Job.to_dict()["estimativa_segundos"]`,
rodada 3): `paginas_totais × _segundos_por_pagina` — informação neutra,
exposta sempre que o número de páginas é conhecido (não só durante
`"processando"`), tanto em `GET /api/jobs` quanto na resposta de
`POST /api/jobs`. `_amostras_ema` conta quantos jobs já alimentaram a EMA;
abaixo de `_AMOSTRAS_PARA_CONFIANCA` (3), `estimativa_baixa_confianca` vai
`true` — logo após subir o processo a EMA vale só o palpite inicial (2s/
página) e não conhece a máquina. O *banner* de aviso na UI é condicional
(acima de `AVISO_ESTIMATIVA_MINUTOS`, ver `dependencies.md`); a estimativa
em si não é.

Como o Docling não expõe um callback nativo por página, "página atual" de um
job em andamento é uma projeção: `decorrido / segundos_por_pagina`, limitada
ao total de páginas. `segundos_por_pagina` começa num palpite razoável (2s) e
se ajusta por média móvel exponencial a cada job concluído — depois de
alguns jobs, a estimativa converge para o throughput real da máquina
naquele processo. A API sempre marca esse número com `"estimado": true`
enquanto o job está em andamento, para a UI deixar claro que não é exato.

### Posição na fila

```python
def _posicoes_na_fila() -> dict[str, int]:
    na_fila = [j for j in _jobs_ordenados() if j.status == "na_fila"]
    return {j.id: i + 1 for i, j in enumerate(na_fila)}
```

Recalculada a cada chamada de `listar_com_progresso()`/`obter_com_progresso()`
a partir da ordem de criação — não é um campo armazenado no `Job`, então
não pode ficar dessincronizada quando um job termina ou é removido no meio
da fila.

### Remoção e limpeza (`remover`, `limpar_finalizados`)

```python
def remover(job_id: str) -> None:
    job = _store.obter(job_id)
    if job is None:
        return
    _apagar_arquivo(job.caminho_pdf)
    _apagar_arquivo(job.caminho_saida)
    _store.remover(job_id)
```

`remover()` assume que o chamador (a rota) já validou existência e que o
status não é `processando` — mesmo padrão de responsabilidade dividida que
`baixar_job` já usava. `_apagar_arquivo` usa `Path.unlink(missing_ok=True)`,
então remover um job cujo arquivo já sumiu do disco não é um erro.
`limpar_finalizados()` itera os jobs em ordem de criação e remove todo
`concluido`/`erro`, devolvendo a contagem — é o que a UI chama a partir do
botão "Limpar finalizados". Sem essa dupla (rota + serviço), os arquivos em
`backend/uploads/` cresceriam sem limite durante a vida do processo, já que
nada mais os apaga.

### `jobs_concluidos()` — seleção para download

```python
def jobs_concluidos(ids: list[str] | None = None) -> list[Job]:
    candidatos = _jobs_ordenados()
    if ids is not None:
        permitidos = set(ids)
        candidatos = [j for j in candidatos if j.id in permitidos]
    return [j for j in candidatos if j.status == "concluido" and j.caminho_saida is not None]
```

Usada por `GET /api/download-zip`. Sem `ids`, pega todos os concluídos
("Baixar tudo"); com `ids`, filtra — e ids inexistentes/pendentes são
silenciosamente ignorados, porque decidir o que é válido baixar é
responsabilidade de quem chamou (a UI só manda ids de jobs que ela mesma
sabe que existem).

## `services/motor_pool.py` — singleton do motor

```python
_motor: m.MotorBase | None = None
_cfg: m.Config | None = None

def inicializar(cfg: m.Config | None = None) -> m.MotorBase:
    global _motor, _cfg
    _cfg = cfg if cfg is not None else m.Config()
    m.aplicar_ambiente(_cfg)
    _motor = m.selecionar_motor(_cfg)
    return _motor
```

Um módulo com estado global no lugar de uma classe/DI container — deliberado
para um único processo de aplicação sem múltiplos "tenants" ou
configurações concorrentes. `obter_motor()`/`obter_config()` levantam
`RuntimeError` explícito se chamadas antes de `inicializar()`, para falhar
alto (e cedo, nos testes) em vez de silenciosamente operar sobre `None`.

Hoje `inicializar()` sempre usa `Config()` (todos os padrões: engine `auto`,
OCR `rapidocr`, tabelas `accurate`) — não há endpoint para o cliente
escolher engine/idioma/qualidade por job; é a mesma configuração para todos
os jobs de um processo. Isso é consistente com o modelo de um motor
compartilhado por processo: mudar `ocr_engine` ou `tables` por job exigiria
reconstruir o `DocumentConverter` (caro) a cada chamada com config diferente.

## Estratégia de testes

- `test_app.py` sobe a aplicação real via `TestClient(app)` (contexto
  `with`, o que dispara o `lifespan`). A classe `TestCriarJobsEndpoint`
  força `motor_pool.inicializar` a sempre construir `Config(engine="simples")`
  via `patch.object`, para que jobs enfileirados de verdade processem rápido
  e sem depender de o Docling estar instalado na máquina de teste.
- `TestDownloadEndpoints` e `TestRemoverJobsEndpoints` inserem `Job`s
  **diretamente no `JobStore`** (sem passar pela fila/worker) porque o que
  está sob teste é "servir/apagar o que já foi processado", não o
  processamento em si — mais rápido e determinístico do que esperar um job
  real terminar.
- `test_jobs.py` testa o módulo de serviço isoladamente, com um
  `_MotorDeTeste` (stub que registra ordem de chamadas, com atraso e falha
  configuráveis) para verificar que dois jobs processam em série e na ordem
  de chegada, sem tocar em Docling/pypdfium2 reais.
- Todos os testes que tocam `JobStore`/`DIR_UPLOADS` trocam o estado global
  em `setUp`/`tearDown` (`jobs._store = jobs.JobStore()`,
  `jobs.DIR_UPLOADS = Path(tmp)`) e restauram no `tearDown` — necessário
  porque esses são módulos com estado de processo, não instâncias isoladas
  por teste.
