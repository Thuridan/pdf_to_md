# Bibliotecas — o que cada dependência faz e como é usada

Cada dependência direta do projeto, o que ela resolve, e exatamente onde
o código a chama. Para a estratégia de versionamento/instalação, ver
[`dependencies.md`](dependencies.md).

## Motor de conversão

### Docling (`docling[rapidocr]`)

Biblioteca da IBM Research para conversão de documentos com preservação de
estrutura — é o que dá ao projeto layout semântico, reconstrução de tabelas
e OCR, em vez de só extração de texto bruto. Usada inteiramente dentro de
`MotorDocling` ([`code.md`](code.md#motordocling--alta-fidelidade)), com
imports preguiçosos por causa da ordem de variáveis de ambiente
([`code.md`](code.md#por-que-os-imports-do-docling-são-preguiçosos)).

APIs consumidas diretamente:

| Símbolo | Módulo | Uso no projeto |
|---|---|---|
| `DocumentConverter`, `PdfFormatOption` | `docling.document_converter` | Instância única por processo/lote, configurada com as opções de pipeline. |
| `InputFormat` | `docling.datamodel.base_models` | Mapeia `PDF` para as opções de formato passadas ao converter. |
| `PdfPipelineOptions`, `TableFormerMode` | `docling.datamodel.pipeline_options` | Liga/desliga OCR e tabelas; escolhe modo `ACCURATE` vs `FAST` do TableFormer. |
| `RapidOcrOptions`, `EasyOcrOptions`, `TesseractOcrOptions` | `docling.datamodel.pipeline_options` | Uma classe de opções por `--ocr-engine` escolhido. |
| `AcceleratorDevice`, `AcceleratorOptions` | `docling.datamodel.accelerator_options` (com fallback para `pipeline_options` em versões antigas) | Repassa `--device`/`--threads` ao pipeline. |
| `resultado.document.export_to_markdown()` | resultado de `.convert()` | Serialização final para a string Markdown retornada por `MotorDocling.converter()`. |

Dois estágios internos do Docling têm caminhos de hardware independentes —
documentado em detalhe no [README](README.md#aceleração-por-gpu): layout +
TableFormer rodam em PyTorch (`--device`), OCR roda via RapidOCR/ONNX
Runtime (`--ocr-backend`). `python pdf_to_md.py --hardware`
(`relatar_hardware()` em `pdf_to_md.py`) introspecciona `torch` e
`onnxruntime` diretamente para reportar o que a máquina realmente suporta,
sem depender de o Docling expor isso.

### RapidOCR

Motor de OCR baseado em ONNX Runtime, trazido como dependência opcional do
Docling (`docling[rapidocr]`) e o `--ocr-engine` padrão do projeto. Duas
características moldam o código:

- **Um idioma por execução** — `_opcoes_ocr()` em `pdf_to_md.py` usa só
  `idiomas[0]` e avisa se mais de um foi passado (EasyOCR e Tesseract não
  têm essa limitação).
- **Modelos vêm do ModelScope, não do Hugging Face Hub** — `--offline`
  (que seta `HF_HUB_OFFLINE`) não bloqueia o download de modelos do
  RapidOCR; só afeta layout/TableFormer.

`--ocr-backend` escolhe entre `onnxruntime` (padrão), `openvino`, `paddle` e
`torch` como motor de inferência por trás do RapidOCR — cada um habilita um
conjunto diferente de *execution providers* de GPU (DirectML, CUDA, OpenVINO,
ROCm), listados por `--hardware`. Pegadinha documentada no README: DirectML
só é ativado pelo Docling quando `--device` resolve para `auto`; forçar
`cpu`/`cuda` explicitamente desativa o DirectML no estágio de OCR mesmo que
o `--ocr-backend` continue `onnxruntime`.

### pypdfium2

Binding Python para o PDFium (o motor de renderização de PDF do Chromium).
Usado em três pontos, todos por ser leve e não carregar nenhum modelo:

1. **`MotorSimples.converter()`** — o motor de fallback: só lê
   `pagina.get_textpage().get_text_bounded()` de cada página, sem OCR nem
   detecção de layout.
2. **`contar_paginas()`** — pré-checagem barata de número de páginas usada
   tanto por `--max-pages` na CLI quanto por `criar_job()` no backend (para
   preencher `paginas_totais`, que alimenta a estimativa de progresso da UI —
   ver [`backend.md`](backend.md#estimativa-de-progresso-ema)).
3. **`--jobs N` com `--engine simples`** — paraleliza via
   `ProcessPoolExecutor` porque abrir um `PdfDocument` por processo é barato,
   ao contrário de recarregar o Docling.

Extra `simples` no `pyproject.toml`; também é uma dependência transitiva do
extra `docling`, então instalar qualquer um dos dois a torna disponível.

## Camada web

### FastAPI

Framework web usado para toda a API (`backend/src/app.py`,
`backend/src/routes/api.py`). Escolhido pela combinação de tipagem via
type hints do Python (`UploadFile`, `list[UploadFile]`, retorno `-> dict`
viram validação/serialização automáticas), geração automática de OpenAPI
(não explorada ativamente pela UI, mas disponível em `/docs`), e
`TestClient` embutido (usado em toda a suíte `backend/tests/test_app.py`
via `starlette.testclient`). `app.mount(..., StaticFiles(...))` reaproveita
a própria FastAPI/Starlette para servir o frontend estático, sem precisar
de um servidor HTTP separado na frente.

### uvicorn (`uvicorn[standard]`)

Servidor ASGI que efetivamente roda a aplicação FastAPI — invocado por
`start.sh` (`python3 -m uvicorn backend.src.app:app --host ... --port ...`)
e sugerido no README para desenvolvimento com `--reload`. O extra
`[standard]` traz `uvloop` (event loop mais rápido) e `httptools` (parser
HTTP em C) quando disponíveis na plataforma — sem eles, uvicorn cai para as
implementações puras em Python, mais lentas mas funcionalmente idênticas.

### python-multipart

Dependência exigida pelo próprio FastAPI para decodificar corpos
`multipart/form-data` — é o que faz `files: list[UploadFile] = File(...)`
em `POST /api/jobs` funcionar. Não é importada diretamente em nenhum código
do projeto; é uma dependência declarada explicitamente no `pyproject.toml`
(extra `web`) porque o FastAPI só a torna obrigatória em tempo de execução
(falha ao processar upload, não na importação), então declará-la
explicitamente evita esse erro tardio de "esqueci de instalar".

## Só para testes

### fpdf2

Gera arquivos PDF sintéticos e válidos em tempo de teste
(`FPDF().add_page()...output(...)`), tanto em `test_pdf_to_md.py` quanto em
`backend/tests/test_jobs.py` (função `_gerar_pdf_valido`). Evita depender de
binários PDF fixos versionados no repositório para exercitar os motores de
verdade (contagem de páginas, extração de texto pelo `MotorSimples`) — os
testes podem gerar PDFs com N páginas e conteúdo controlado sob demanda.
Extra `dev` no `pyproject.toml`.

## Ausentes por design

- **Nenhuma dependência de frontend** (sem React/Vue/bundler/npm) — ver
  [`frontend.md`](frontend.md#decisão-fundamental-sem-build-step).
- **Nenhum ORM/driver de banco** — não há persistência além de arquivos em
  disco e estado em memória (ver
  [`architecture.md`](architecture.md#persistência-ou-a-ausência-dela)).
- **Nenhum broker de fila** (Celery/Redis/RabbitMQ) — a fila é um
  `queue.Queue` em memória de processo único
  ([`backend.md`](backend.md#fila-e-worker-único)).
