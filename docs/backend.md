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

### Medição: OCR recupera capturas de tela em documento nativo? (rodada 4, TAREFA-1)

> **Errata (rodada 5, TAREFA-5):** a conclusão abaixo — "não se sustentou
> para os dois exemplos que a motivaram" — estava **errada**. O OCR sempre
> recuperou os cinco termos-alvo nas duas páginas; o texto ficava
> pendurado como filhos do `PictureItem` na árvore do `DoclingDocument` e
> nunca era exportado porque `export_to_markdown()` não recebia
> `traverse_pictures=True` (parâmetro específico para esse caso, que o
> projeto nunca passou). A medição de tempo/RSS abaixo continua correta e
> válida — é a leitura de "conteúdo não recuperado" que estava errada,
> por medir só o `.md` final sem esse parâmetro. Ver "Matriz de OCR:
> idioma, escala e o achado real" (rodada 5, TAREFA-5) para a investigação
> completa, e `README.md` para a correção equivalente no texto voltado ao
> usuário. Texto original preservado abaixo, sem edição, para manter o
> histórico da medição.

A rodada 4 partiu da hipótese de que ligar OCR num documento nativo
recuperaria o texto preso dentro de capturas de tela grandes (o
`<!-- image -->` no Markdown vira um buraco de conteúdo silencioso). A
hipótese foi **testada e não se sustentou para os dois exemplos que a
motivaram** — por isso a rodada parou aqui: as TAREFA-2 e TAREFA-3 (segundo
sinal de detecção e exposição de `ocr_origem`) **não foram feitas**.

**Método:** faixa real de 46 páginas (50–95) extraída de `teste.pdf` via
`pypdfium2.PdfDocument.import_pages()` — inclui as duas capturas citadas no
prompt da rodada (página 61: XML diff de Config Audit; página 91: tela de
Logging and Reporting Settings). Convertida duas vezes, `ocr=False` e
`ocr=True`, com `settings.debug.profile_pipeline_timings = True`.

| Medida | `ocr=False` | `ocr=True` |
|---|---|---|
| Tempo total | 114,27 s | 157,69 s (1,38×) |
| Pico de RSS (`/usr/bin/time -v`) | 18,72 GB | 44,31 GB (2,37×) |
| Estágio `ocr` (`conv_res.timings`) | inexistente | 135,69 s (86% do tempo total) |
| `layout` / `table_structure` | 31,35 s / 17,23 s | 30,64 s / 16,99 s (~iguais) |

Os cinco termos verificados manualmente na página 61
(`scp_admin`, `password-complexity`, `update-server`, `Max Rows in CSV
Export`, `corp-syslog`) foram procurados nos dois `.md` de saída:
**nenhum apareceu em nenhum dos dois modos** (`scp_admin` aparece nos dois,
mas é falso positivo — um exemplo de comando SCP legítimo em outra página da
mesma faixa, sem relação com a captura da página 61). A mesma checagem na
região da página 91 (Logging and Reporting Settings): o `<!-- image -->`
correspondente não ganhou nenhum texto ao redor entre os dois modos.

Um `diff` entre os dois `.md` mostra que o OCR **funciona mecanicamente** —
recupera texto real e coerente de várias outras capturas na mesma faixa
(diálogo de busca "Global Find", árvores de checkbox de permissão como "PDF
Summary Reports"/"User Activity Report", um parágrafo inteiro de exemplo de
perfil de administrador). Mas nas duas capturas que motivaram a rodada,
o resultado foi: nada de útil perto da imagem da página 91 (placeholder
ficou vazio antes e depois), e só fragmentos garbled perto da região da
página 61 ("Lon Sottinae", "Version 1", "Version 6" — nenhum termo técnico
recuperável). O log de execução registrou exatamente duas ocorrências de
`RapidOCR returned empty result!`, consistente com falha silenciosa em
pelo menos parte dessas capturas específicas.

**Leitura:** o sinal não é "OCR nunca ajuda" — é "OCR ajuda em capturas
simples de UI (diálogos, checkboxes, menus) e falha em capturas densas de
texto técnico monoespaçado/tabular (diff de XML, tela de configuração com
grade de campos)", que são justamente o tipo de captura mais valioso para
recuperar. Adicionar um segundo sinal de detecção por área de imagem
(TAREFA-2) ligaria OCR automaticamente para documentos como `teste.pdf` e
pagaria o custo medido acima sem necessariamente recuperar o conteúdo que
motivou a mudança — e o custo é real: 44,3 GB de pico numa faixa de só 46
páginas é compatível com o pico de ~44,2 GB já observado convertendo o
`teste.pdf` inteiro (1310 páginas) **sem** OCR, o que sugere que ligar OCR
amplamente num documento desse tamanho arriscaria OOM sem que o problema de
gestão de memória (rodada futura, fora do escopo desta) esteja resolvido.

Por isso a heurística atual (`tem_camada_de_texto()`, rodada 3 TAREFA-3)
**não foi alterada** nesta rodada — ela já é a decisão apropriada dado que a
correção proposta não entrega o benefício esperado, ao custo medido acima.
Ver `bug_report-4.md` para o registro completo (evidências, comandos e
classificação) e a avaliação (não implementada) do `<!-- image -->` em si.

### Decisão registrada (não implementada): embutir imagens no Markdown (rodada 4, TAREFA-4)

O `<!-- image -->` é um problema **separado** do OCR e continua existindo
mesmo com a heurística de detecção como está — é assim que
`export_to_markdown()` representa qualquer figura, recuperada por OCR ou
não. Quem controla isso são as opções de imagem do pipeline
(`generate_picture_images`, `images_scale`) e o modo de imagem do export;
o projeto hoje não configura nenhuma das duas. Avaliação, sem
implementação, por instrução explícita do prompt desta rodada:

- **O que seria preciso:** ligar `generate_picture_images=True` nas
  `PdfPipelineOptions` faz o Docling manter o bitmap recortado de cada
  figura anexado ao `PictureItem`; o modo de imagem do
  `export_to_markdown()` (`ImageRefMode.EMBEDDED` ou `REFERENCED`) decide
  se isso vira `data:` URI inline no `.md` ou um arquivo separado ao lado
  dele. Embutido é mais simples para o usuário levar um único arquivo;
  referenciado evita inflar o `.md` mas exige empacotar e servir os
  arquivos junto (a rota de download já lida só com um `.md` por job hoje).
- **O custo:** `generate_picture_images` mantém os bitmaps de página em
  memória durante toda a conversão, além do que layout/TableFormer/OCR já
  usam — soma diretamente ao pico de RSS. O pico de 44 GB já observado
  convertendo o `teste.pdf` inteiro (sem essa opção) já é preocupante por
  si só (ver rodada 3, TAREFA-4); ligar isso sem antes resolver a gestão de
  memória (rodada futura) é arriscar OOM em qualquer documento grande com
  muitas imagens — e `teste.pdf` tem 1357 imagens.
- **Escopo, por job ou global:** faz mais sentido como opção por job, no
  mesmo padrão do seletor de OCR (`modo_ocr`) — um usuário processando um
  manual com capturas relevantes pagaria o custo deliberadamente, sem
  forçá-lo em todos os outros jobs da fila.

Este item é insumo para a rodada de memória; não foi antecipado.

### Graus de confiança do Docling (rodada 5, TAREFA-3)

O Docling calcula um `confidence` (`ConfidenceReport`) por conversão desde
a v2.34.0: quatro escores por página (`layout_score`, `ocr_score`,
`parse_score`, `table_score` — este último ainda não implementado na
versão instalada) agregados em graus (`POOR`/`FAIR`/`GOOD`/`EXCELLENT`),
tanto por documento quanto por página (`confidence.pages`, um dict indexado
pelo número real da página — verificado num extrato real de 46 páginas do
`teste.pdf`: chaves `1..46`, o mesmo índice de `_marcador_pagina()` da
TAREFA-1). `_extrair_confianca()` em `pdf_to_md.py` lê **só os graus**
(`mean_grade`), nunca os escores numéricos — a própria documentação do
Docling recomenda isso, porque o cálculo/ponderação dos escores pode mudar
entre versões. "Página com grau baixo" = `mean_grade` da página abaixo de
`GOOD` (`POOR` ou `FAIR`) — a leitura mais direta de "esta página pode
precisar de revisão manual".

`MotorBase.converter()` passou a devolver `ResultadoMotor` (markdown +
`grau_medio` + `paginas_grau_baixo`) em vez de só a string — `MotorSimples`
não roda o pipeline do Docling, então sempre devolve `grau_medio=None`.
`converter_arquivo()` repassa os dois campos para `Resultado`;
`jobs._processar()` copia de `Resultado` para `Job.grau_medio`/
`Job.paginas_grau_baixo`, expostos por `to_dict()`. Grau baixo **nunca**
vira erro — é sinalizado (log de aviso na CLI, campo visível no job na
API/frontend), a conversão segue utilizável.

Medido no mesmo extrato real de 46 páginas: grau do documento
`excellent` (`mean_score` ≈ 0,92), com **1 de 46 páginas** em `fair`
(página 39 da faixa) — nenhuma em `poor`. Consistente com a natureza do
documento (nativo, texto rico) e útil como prova de que o sinal distingue
páginas de fato: as outras 45 páginas ficaram `excellent`/`good`.

### Retomada de jobs no startup (rodada 5, TAREFA-4)

A fila de jobs vive só em memória (`JobStore`) — se o processo cai no meio
de um lote, a fila some, mas os `.md` já gerados continuam no disco em
`uploads/`, sem nenhum job apontando para eles. `jobs.retomar_jobs_do_disco()`
roda no `lifespan()` do FastAPI, **antes** de `iniciar_worker()`: varre
`uploads/*.pdf` e reconstitui um `Job` com `status="concluido"` para todo
par `{job_id}.pdf` + `{job_id}.md` já completo.

Um `.pdf` **sem** `.md` correspondente fica de fora, deliberadamente — não
tem como saber se ele parou por falha de conversão ou por interrupção no
meio do processamento, e reenfileirar às cegas arriscaria repetir uma
falha ou gastar tempo num arquivo problemático. Só é contado e logado
("N concluído(s) reconstituído(s), M órfão(s) sem `.md`"); decidir o que
fazer com os órfãos fica com o usuário. Varredura por idade (limpar
uploads velhos) fica para a rodada de isolamento — não antecipada aqui.

**Limitações registradas, por não haver metadados persistidos ao lado do
PDF:**
- `nome_original` não é recuperável (o upload é gravado como
  `{job_id}.pdf`, sem nada além disso) — o job retomado usa o próprio
  nome do arquivo em disco como nome exibido.
- `ocr_origem` vira `"desconhecido"` em vez de um valor inventado — a
  decisão de OCR efetiva da execução original não fica gravada em lugar
  nenhum. O frontend trata esse valor suprimindo o rótulo de OCR na linha
  do job, em vez de mostrar `"detectado"`/`"forçado"` como se fosse fato
  quando não é.

Persistir um `.json` de metadados ao lado do PDF resolveria os dois, mas é
mudança maior (grava e lê um arquivo extra por job, formato para manter
compatível) — avaliado e descartado para esta rodada por ser
desproporcional ao problema que motivou a tarefa; fica registrado aqui
como opção futura se as limitações acima incomodarem na prática.

### Matriz de OCR: idioma, escala e o achado real (rodada 5, TAREFA-5)

**Medição e registro — nenhum código de produção foi alterado por esta
tarefa.** O objetivo era isolar por que o OCR não recuperou o conteúdo das
páginas 61 e 91 do `teste.pdf` (achado da rodada 4), testando três fatores
não controlados: idioma, escala do RapidOCR, e motor de OCR.

**Método:** as duas páginas extraídas isoladamente (`pypdfium2.import_pages`,
uma página por PDF), convertidas repetidas vezes variando um parâmetro por
vez, medindo tempo, pico de RSS e presença dos termos-alvo
(`password-complexity`, `update-server`, `Max Rows in CSV Export`,
`corp-syslog`, mais `scp_admin` como quinto termo).

**(a) Idioma — REFUTADO, e por um motivo mais direto que o esperado.**
`lang=["pt"]` e `lang=["en"]` produziram saída **byte a byte idêntica** nas
duas páginas. O log do RapidOCR mostra por quê: os dois carregam
exatamente o mesmo arquivo de modelo de reconhecimento
(`PP-OCRv6_rec_small.onnx`) — nesta instalação (tier "small"), o parâmetro
`lang` não troca de modelo entre português e inglês. A premissa do prompt
("PP-OCRv6 trata os dois como modelos distintos") não se confirmou para o
tier instalado.

**(b) Escala — também REFUTADO.** `scale` em 1.0, 3.0 (padrão) e 6.0
produziram a mesma saída idêntica, nas duas páginas. Upscaling/downscaling
não é o fator.

**(c) Motor alternativo — não testável neste ambiente.** Sem GPU NVIDIA
(`nvidia-smi` ausente, nenhum dispositivo CUDA detectado): Nemotron-OCR
fica fora de alcance. Tesseract não está instalado e o ambiente não tem
`sudo` sem senha para instalá-lo — não testado. Registrado como bloqueio
de ambiente, não como resultado.

**O achado real, encontrado ao investigar por que (a) e (b) não mudavam
nada:** com `force_full_page_ocr=True` (bypassa o filtro de região
`PDF_AWARE_LAYOUT_REGIONS` por completo) o resultado continuou **idêntico**
— sinal de que o problema não era o OCR não rodar na imagem certa. Isso
levou a inspecionar a assinatura de `export_to_markdown()` mais a fundo:
ela tem um parâmetro `traverse_pictures: bool = False`, com o comentário
"*Must be set to True for scanned/image-based PDFs processed with
full-page OCR, where the layout model places all OCR text as children of
a top-level PictureItem*". O projeto **nunca passou esse parâmetro**.

Testado: `resultado.document.export_to_markdown(traverse_pictures=True)`
na mesma conversão (mesmo `ConversionResult`, nada reprocessado) —

| Página | Termos-alvo recuperados sem `traverse_pictures` | Termos-alvo recuperados com `traverse_pictures=True` |
|---|---|---|
| 61 | nenhum (0/3 aplicáveis) | `scp_admin`, `password-complexity`, `update-server` (3/3) |
| 91 | nenhum (0/2 aplicáveis) | `Max Rows in CSV Export`, `corp-syslog` (2/2) |

**Os cinco termos-alvo da rodada 4 aparecem, todos.** O OCR sempre
recuperou esse conteúdo — o texto reconhecido fica pendurado como filhos
do `PictureItem` na árvore do documento, e `export_to_markdown()` só
desce nessa filhos quando `traverse_pictures=True`. Sem esse parâmetro, o
texto existe no `DoclingDocument` e é descartado silenciosamente na
serialização — o achado da rodada 4 ("OCR falha nas capturas densas") mediu
o sintoma certo com o instrumento errado: a medição do `.md` de saída, sem
esse parâmetro, é cega para o que o OCR de fato produziu.

**Custo de ligar isso:** desprezível — é um parâmetro puro de serialização
sobre um `ConversionResult` já pronto, não dispara reprocessamento. Medido:
~6ms → ~10ms por chamada de `export_to_markdown()` na página 61. O custo de
memória do OCR em si (2,37× de RSS, rodada 4) **não muda** — esse número já
reflete rodar o OCR; `traverse_pictures` só decide se o resultado aparece
no `.md` ou fica preso na árvore, sem custo adicional de conversão.

**Formato do texto recuperado, com ressalva:** o texto sai como uma
sequência linear de fragmentos (um por célula/linha detectada pelo OCR),
sem preservar a estrutura de tabela/coluna original — legível e buscável,
mas não formatado como a tabela visual do XML Diff. Ainda assim, muito
mais útil que `<!-- image -->` vazio: todos os termos técnicos ficam
presentes e buscáveis.

**Não implementado nesta rodada** (restrição explícita do prompt — TAREFA-5
é só medição). Ver TAREFA-6 para a correção do relatório da rodada 4 à luz
deste achado, e a rodada de memória/qualidade seguinte para decidir se
`traverse_pictures=True` vira o padrão do projeto.

### Achatamento de spans em tabelas no Markdown (rodada 5, TAREFA-8)

**Medição e registro — nenhuma mudança no formato de saída.** Markdown não
tem sintaxe de span de célula; o serializador do `docling_core` escreve o
texto da célula mesclada só na posição de origem e deixa as posições
cobertas pelo span vazias (comportamento documentado na própria doc de
serialização do Docling). `export_to_html()`/`export_to_dict()` preservam
`row_span`/`col_span` — Markdown não.

**Método:** amostra real de 70 páginas, espalhada pelo `teste.pdf`
inteiro (1310 páginas) — 5 páginas a cada 100, não um trecho contínuo, para
não enviesar pela estrutura local de uma única seção. Contagem de células
com `row_span > 1` ou `col_span > 1` via `TableItem.data.table_cells`
(`DoclingDocument` real, não amostragem do `.md`).

**Resultado:** 42 tabelas na amostra, **2 com pelo menos uma célula com
span (4,8%)** — proporção baixa. Mas o efeito, quando ocorre, não é uma
célula vazia inofensiva — é **realinhamento silencioso da linha inteira**.
Exemplo real (página 10 da amostra, tabela de privilégios de relatório):
a célula mesclada (`colspan="2"`, cobrindo "Access Level" + o começo de
"Description") vira, no Markdown achatado, uma única célula na coluna
"Access Level" com o texto `"Authentication Specifies whether the
administrator can create a"` — nome do nível de acesso ("Authentication")
grudado no INÍCIO da descrição real. A coluna "Description" da mesma linha
fica com só o resto do texto: `"custom report that includes data from the
Authentication logs."`. A linha continua com 6 células (o número certo de
colunas) — não há nada estruturalmente quebrado para alertar quem lê — mas
o conteúdo de duas colunas está migrado, silenciosamente. Comparação HTML
lado a lado (mesma tabela, mesma linha):

```html
<td colspan="2">Authentication Specifies whether the administrator can create a</td>
<td>custom report that includes data from the Authentication logs.</td>
```

Isso é exatamente o risco que motivou a tarefa: alguém perguntando "qual o
nível de acesso de X" a partir do Markdown teria a resposta certa
misturada com a descrição errada, com aparência de tabela válida — falha
silenciosa, sem erro visível em lugar nenhum.

**Leitura:** a proporção medida (4,8%) é baixa o bastante para não virar
decisão de produto imediata (limiar do próprio prompt: "se a proporção for
baixa, registra-se e segue") — mas a amostra é de 70 páginas de 1310
(5,3%), não exaustiva, e o efeito por ocorrência é sério o bastante
(resposta errada com aparência de certa, num manual de referência) para
que qualquer decisão futura sobre exportar HTML junto do `.md` (opção A) ou
escrever um `TableSerializer` que repita o valor nas células cobertas em
vez de deixá-las vazias (opção B) mereça uma amostra maior antes de
descartar o problema como raro. Não implementado nesta rodada, por
instrução explícita.

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

### Estimativa de progresso (EMA por modo de OCR — rodada 3, TAREFA-5)

```python
_SEGUNDOS_POR_PAGINA_PADRAO = 2.0
_ALPHA_EMA = 0.3
_segundos_por_pagina: dict[bool, float] = {True: 2.0, False: 2.0}
_amostras_ema: dict[bool, int] = {True: 0, False: 0}

def _atualizar_estimativa(job: Job) -> None:
    if not job.paginas_totais:
        return
    observado = job.segundos / job.paginas_totais
    atual = _segundos_por_pagina[job.ocr]
    _segundos_por_pagina[job.ocr] = _ALPHA_EMA * observado + (1 - _ALPHA_EMA) * atual
    _amostras_ema[job.ocr] += 1
```

**Por que por modo, não uma EMA global:** a TAREFA-4 (mesma rodada) deu ao
motor Docling um `DocumentConverter` por modo de OCR justamente porque
`do_ocr` muda o pipeline; o custo por página muda junto, em ordem de
grandeza (rodar RapidOCR página a página é um trabalho fundamentalmente
diferente de só extrair layout/tabelas). Uma EMA global misturando os dois
não convergiria para nada útil para nenhum dos dois — ela ficaria oscilando
entre "rápido" e "lento" conforme o mix de jobs recentes, exatamente o
problema que motivou a TAREFA-5. As duas EMAs começam no mesmo palpite
inicial (2 s/página): não havia, nesta rodada, uma medição própria por modo
que justificasse valores iniciais diferentes sem calibrar no vazio — cada
uma converge para a realidade da máquina assim que os primeiros jobs
daquele modo terminam (ver `_AMOSTRAS_PARA_CONFIANCA`, abaixo).

`_processar()` só chama isso quando `resultado.status == "ok"` — um job
`"pulado"` (saída já existia, ver `converter_arquivo`) também conta como
concluído do ponto de vista do usuário, mas `resultado.segundos` nesse caso
é `0.0`; alimentar isso na média móvel faria a estimativa de progresso
convergir para "instantâneo", o que é falso para as conversões reais.

**Estimativa de duração total** (`Job.to_dict()["estimativa_segundos"]`,
TAREFA-2, agora usando a EMA do **modo do próprio job** — TAREFA-5):
`paginas_totais × _segundos_por_pagina[job.ocr]` — informação neutra,
exposta sempre que o número de páginas é conhecido (não só durante
`"processando"`), tanto em `GET /api/jobs` quanto na resposta de
`POST /api/jobs`. `_amostras_ema[job.ocr]` conta quantos jobs *daquele
modo* já alimentaram a EMA; abaixo de `_AMOSTRAS_PARA_CONFIANCA` (3),
`estimativa_baixa_confianca` vai `true` — logo após subir o processo (ou
antes do primeiro job de um modo específico) a EMA daquele modo vale só o
palpite inicial e não conhece a máquina. O *banner* de aviso na UI é
condicional (acima de `AVISO_ESTIMATIVA_MINUTOS`, ver `dependencies.md`);
a estimativa em si não é.

**Decisão registrada (TAREFA-5 pedia para avaliar e registrar, não
necessariamente implementar):** dentro de um job longo, `pagina_estimada`
(`to_dict()`, projeção "página atual" de um job `"processando"`) é uma
projeção linear pelo tempo decorrido (`decorrido / segundos_por_pagina`) —
imprecisa num job de 90 minutos, mas visível só ali, não em jobs curtos.
Usar "a taxa observada do próprio documento" em vez da EMA global/por-modo
**não é implementável nesta rodada**: o Docling não expõe nenhum callback
de progresso por página (`architecture.md`/`code.md` já documentam isso),
então não existe nenhum sinal intermediário durante a conversão de UM job
específico para calibrar contra — só temos a duração total, uma vez, no
fim. A única melhoria de fato alcançável com o que o Docling expõe hoje é
diferenciar por modo de OCR, que é exatamente o que esta tarefa já faz.
Refinar mais (ex.: heurísticas por contagem de páginas/complexidade) seria
especular sem dado real para calibrar — mesmo problema que motivou usar
documentos reais, não sintéticos, para calibrar a detecção de OCR na
TAREFA-3. Fica para uma rodada futura, condicionado a alguma fonte de sinal
intermediário (ex.: se uma versão futura do Docling expuser callback por
página).

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
