# pdf_to_md — Conversor PDF → Markdown (v2.1)

## Visão geral

Utilitário de linha de comando que converte PDFs em Markdown estruturado, priorizando
extração semântica e preservação de tabelas complexas via **Docling** (layout model +
TableFormer + OCR). Opera em modo automatizado (parâmetros) ou interativo, processa
arquivos avulsos ou diretórios inteiros, e limita o consumo de CPU durante a inferência
local dos modelos.

## Arquitetura

| Camada | Responsabilidade |
|---|---|
| **CLI (`argparse`)** | Valida parâmetros, define códigos de saída e ativa o modo interativo quando `-i` é omitido. |
| **Ambiente (`aplicar_ambiente`)** | Injeta `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS` e, com `--offline`, `HF_HUB_OFFLINE`. Roda **antes** do import do Docling — por isso o import é preguiçoso, dentro do motor. |
| **Motores** | `MotorDocling` (alta fidelidade) e `MotorSimples` (`pypdfium2`, só camada de texto). `--engine auto` escolhe o Docling e cai para o simples se ele não estiver instalado. |
| **Caminhos** | `coletar_pdfs` expande arquivos/diretórios com deduplicação; `resolver_saida` decide arquivo vs. diretório e espelha a subárvore no modo recursivo. |
| **Escrita** | `escrever_atomico` grava em `.tmp` e faz `rename`, evitando `.md` truncado se a conversão falhar no meio. |

Uma única instância de `DocumentConverter` é reaproveitada em todo o lote — os modelos
são carregados uma vez, não uma vez por arquivo.

Documentação de design mais detalhada, por camada:

| Documento | Conteúdo |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Arquitetura do sistema como um todo: motor + backend + frontend. |
| [`docs/code.md`](docs/code.md) | Design do motor de conversão/CLI (`pdf_to_md.py`). |
| [`docs/backend.md`](docs/backend.md) | Design da API web (FastAPI, fila, worker). |
| [`docs/frontend.md`](docs/frontend.md) | Design da UI web (HTML/CSS/JS, sem build step). |
| [`docs/dependencies.md`](docs/dependencies.md) | Como as dependências são declaradas, versionadas e instaladas. |
| [`docs/library.md`](docs/library.md) | O que cada biblioteca de terceiros faz e onde é usada. |

## Estrutura do projeto

```
pdf_to_md/
├── pdf_to_md.py           # motor de conversão + CLI (usado pela linha de comando e pelo backend)
├── test_pdf_to_md.py
├── backend/               # API web (FastAPI) por cima de pdf_to_md.py
│   ├── src/
│   │   ├── routes/api.py       # camada HTTP: valida requests, chama services/
│   │   ├── services/
│   │   │   ├── jobs.py         # fila de conversão (worker único sequencial)
│   │   │   └── motor_pool.py   # instância única do motor por processo
│   │   └── app.py              # cria o FastAPI, monta rotas + frontend estático
│   └── tests/
├── frontend/              # UI estática (HTML/CSS/JS puro, sem build) servida pelo backend
├── docs/                  # documentação de design (arquitetura, backend, frontend, código, dependências, libs)
├── scripts/               # start.sh / stop.sh / restart.sh (sobem/derrubam o backend em background) + verificar_api_docling.py
└── pyproject.toml
```

`backend/` e `frontend/` são deliberadamente enxutos: sem `models/` (não há banco de
dados — o estado dos jobs vive em memória) nem `middlewares/` (nenhum ainda é
necessário). `routes/api.py` faz o papel de "controller" e `services/` concentra a
lógica de negócio, reaproveitando as mesmas funções (`converter_arquivo`,
`selecionar_motor`) que a CLI usa.

## Instalação

Forma recomendada, via `pyproject.toml` (também instala o comando `pdf-to-md`):

```bash
pip install ".[docling]"   # motor de alta fidelidade (Docling + RapidOCR)
pip install ".[simples]"   # so o motor leve (pypdfium2)
pip install ".[dev]"       # dependencias da suite de testes (fpdf2)
pip install ".[web]"       # FastAPI + uvicorn, para a aplicação web (ver seção abaixo)
```

Forma alternativa, direta (sem clonar o `pyproject.toml`):

```bash
pip install "docling[rapidocr]"   # RapidOCR virou extra opcional a partir do docling 2.x
pip install pypdfium2             # opcional: motor de fallback
```

Pré-carregar os modelos (necessário antes de usar `--offline`):

```bash
docling-tools models download
```

⚠️ **Linux sem ambiente gráfico:** o `opencv-python` (dependência do RapidOCR)
falha com `ImportError: libGL.so.1: cannot open shared object file` em
máquinas headless. Sem acesso root, a correção é trocar o pacote pelo
equivalente sem dependências gráficas, que expõe o mesmo módulo `cv2`:

```bash
pip uninstall -y opencv-python
pip install opencv-python-headless
```

## Uso

```bash
python pdf_to_md.py -i relatorio.pdf                     # saída ao lado do PDF
python pdf_to_md.py -i ./pdfs -o ./md -r --overwrite     # lote recursivo
python pdf_to_md.py -i doc.pdf --no-ocr --tables fast    # PDF nativo, bem mais rápido
python pdf_to_md.py -i scan.pdf --ocr-engine tesseract --lang pt en
python pdf_to_md.py -i ./pdfs -o ./md --engine simples --jobs 4  # lote em paralelo
python pdf_to_md.py -i entrada.pdf --max-pages 50        # recusa PDFs gigantes de cara
python pdf_to_md.py                                      # modo interativo
```

### Parâmetros

| Flag | Descrição |
|---|---|
| `-i, --input` | Um ou mais PDFs e/ou diretórios. |
| `-o, --output` | Arquivo `.md` (uma entrada) ou diretório de saída. |
| `-r, --recursive` | Percorre subdiretórios. |
| `--engine` | `auto` (padrão), `docling`, `simples`. |
| `--ocr / --no-ocr` | OCR ligado por padrão. Desligue em PDFs com texto nativo. |
| `--ocr-engine` | `rapidocr` (padrão), `easyocr`, `tesseract`. |
| `--lang` | ISO 639-1 (`pt`). Convertido para ISO 639-2 no Tesseract (`por`). |
| `--tables` | `accurate` (padrão), `fast`, `none`. |
| `--threads` | Limite de threads de CPU. |
| `--device` | `auto`, `cpu`, `cuda`, `cuda:N`, `mps`, `xpu`. |
| `--ocr-backend` | Backend do RapidOCR: `onnxruntime`, `openvino`, `paddle`, `torch`. |
| `--hardware` | Lista os aceleradores detectados e sai. |
| `--offline`, `--artifacts` | Bloqueia o HF Hub / aponta modelos locais. |
| `--timeout` | Tempo máximo por documento. |
| `--max-pages` | Recusa PDFs com mais páginas que o limite antes de converter (checagem barata via pypdfium2; sem limite por padrão). |
| `--jobs` | PDFs convertidos em paralelo (padrão: 1). Só tem efeito com `--engine simples`; ignorado (com aviso) no Docling, já que cada processo recarregaria os modelos inteiros. |
| `--overwrite`, `--dry-run` | Sobrescrever / simular. |
| `-v, --verbose`, `-q, --quiet` | Nível de log. |

### Códigos de saída

`0` sucesso · `1` alguma falha · `2` erro de uso · `130` interrompido.

## Aplicação web

Interface de upload com fila de conversão, progresso e download individual/em lote,
construída sobre o mesmo `pdf_to_md.py` (um único motor Docling reaproveitado por
processo — mesma garantia da CLI para um lote).

```bash
pip install ".[web]"   # além de ".[docling]" ou ".[simples]", conforme o motor desejado
./scripts/start.sh              # sobe em background (0.0.0.0:8000), aguarda /api/health
./scripts/restart.sh
./scripts/stop.sh
```

Por padrão o servidor escuta em todas as interfaces (`0.0.0.0`), então fica acessível
por outras máquinas na mesma rede via o IP local desta máquina (ex.: `http://10.2.1.175:8000`),
não só via `127.0.0.1`. Não há autenticação nem HTTPS — não exponha essa porta diretamente
à internet; restrinja o acesso pelo firewall (`ufw`) a quem realmente precisa da rede local.
`HOST`/`PORT` são configuráveis por variável de ambiente (`PORT=8001 ./scripts/start.sh`,
`HOST=127.0.0.1 ./scripts/start.sh` para voltar a só loopback). PID e
log ficam em `.run/` (gitignored). Para rodar em primeiro plano com reload:

```bash
uvicorn backend.src.app:app --reload
```

Principais rotas (ver `backend/src/routes/api.py`):

| Rota | Descrição |
|---|---|
| `POST /api/jobs` | Upload de um ou mais PDFs; enfileira para conversão. |
| `GET /api/jobs` | Lista jobs com status/progresso estimado. |
| `GET /api/jobs/{id}` | Status de um job específico. |
| `GET /api/jobs/{id}/download` | Baixa o `.md` de um job concluído. |
| `DELETE /api/jobs/{id}` | Remove um job (e seus arquivos em disco); `409` se ainda estiver processando. |
| `DELETE /api/jobs` | Remove todos os jobs concluídos/com erro (e seus arquivos) — "Limpar finalizados". |
| `GET /api/download-zip` | Zip com os `.md` de todos os jobs concluídos (ou de `?ids=`). |

## Aceleração por GPU

O pipeline tem **dois estágios independentes**, com suportes de hardware diferentes.

**1. Layout + TableFormer (PyTorch)** — controlado por `--device`:

| Hardware | Valor | Observação |
|---|---|---|
| NVIDIA | `cuda` / `cuda:N` | Caminho mais maduro. |
| Apple Silicon | `mps` | |
| Intel Arc / Iris Xe | `xpu` | Exige build do PyTorch com suporte a XPU. |
| AMD no Linux | `cuda` | Builds ROCm do PyTorch se apresentam como CUDA. |
| AMD no Windows | — | Sem suporte; este estágio fica na CPU. |

Se o dispositivo pedido não existir, o Docling cai para CPU em vez de falhar.

**2. OCR (RapidOCR / ONNX Runtime)** — controlado por `--ocr-backend`:

- `onnxruntime` + pacote `onnxruntime-directml` → **DirectML**, que atende qualquer
  GPU DirectX 12 (AMD, Intel, NVIDIA) no Windows.
- `openvino` → GPU integrada e NPU Intel.
- O EP CUDA é usado quando `--device` resolve para CUDA.

⚠️ **Pegadinha importante:** no código do Docling, DirectML é habilitado com
`use_dml = (device == AUTO)`. Ou seja, forçar `--device cpu` ou `--device cuda`
**desliga** o DirectML no OCR. Para usá-lo, mantenha `--device auto`.

Use `python pdf_to_md.py --hardware` para ver o que a máquina realmente oferece.

## Notas sobre OCR

- **RapidOCR** usa **um idioma por execução**; se vários forem passados, apenas o primeiro
  vale (o script avisa). `pt` está na lista PP-OCRv6, então `--lang pt` é válido.
- Os modelos do RapidOCR vêm do **ModelScope**, não do Hugging Face — `--offline`
  bloqueia apenas o HF Hub (layout e TableFormer).
- Para documentos digitais (gerados por editor, não digitalizados), `--no-ocr` reduz
  drasticamente o tempo sem perda de qualidade.

## Reprodutibilidade

O `pyproject.toml` fixa faixas de versão (ex.: `docling[rapidocr]>=2.100,<3`)
em vez de deixar o pip resolver o que houver de mais recente a cada
instalação — evita que uma atualização inesperada de uma dependência (do
Docling ou de qualquer coisa na árvore, como o PyTorch) mude o comportamento
sem aviso. Fixar hash-a-hash cada arquivo (`pip install --require-hashes`)
não é viável aqui: o Docling traz variantes de wheel por plataforma/GPU
(cuda/mps/xpu/cpu) e uma lista de hashes só cobre a combinação da máquina
onde foi gerada.

Para máxima reprodutibilidade em uma máquina com a mesma plataforma, use o
snapshot congelado incluso, gerado com `pip freeze` a partir de uma instalação
testada de ponta a ponta (inclui a troca de `opencv-python` por
`opencv-python-headless` mencionada acima):

```bash
pip install -r requirements-lock.txt
```

Isso é um retrato informativo, não o caminho de instalação principal — para
projetos novos, prefira os extras do `pyproject.toml` (`pip install
".[docling]"`) e deixe o pip resolver a faixa de versões declarada.

## Paralelismo de lote

`--jobs N` paraleliza a conversão de vários PDFs quando `--engine simples`
(extração de texto via `pypdfium2`, sem estado, barata de repetir por
processo). Com `--engine docling`, `--jobs` é ignorado: cada processo teria
que recarregar o layout model + TableFormer + OCR inteiros, trocando RAM por
paralelismo sem ganho real — o motor já reaproveita uma única instância de
`DocumentConverter` para todo o lote dentro de um processo, que é o desenho
mais eficiente disponível hoje.

## Limite de páginas (`--max-pages`)

Documentos corrompidos, adversariais ou simplesmente enormes podem consumir
CPU/memória por tempo indeterminado sem `--timeout` (que continua desligado
por padrão). `--max-pages N` faz uma checagem barata de contagem de páginas
via `pypdfium2` *antes* de acionar o pipeline pesado do Docling — um PDF
acima do limite falha instantaneamente, sem gastar minutos de OCR. Sem
`pypdfium2` instalado, a checagem é pulada (aviso único no log) e a conversão
segue normalmente; a flag não altera o comportamento quando omitida.

## Testes

```bash
python -m unittest test_pdf_to_md -v   # 76 testes (motor de conversão / CLI)
python -m pytest backend/               # suíte da aplicação web (52 testes)
python scripts/verificar_api_docling.py <dir>  # confere a API do Docling estaticamente
```

Duas falhas são esperadas *apenas* se o pacote `docling` real estiver
instalado no mesmo ambiente de teste: `test_auto_cai_para_simples` e
`test_docling_explicito_falha_com_mensagem_util` assumem um ambiente sem
Docling instalado para exercitar o caminho de fallback/erro do
`selecionar_motor`. Isso não é uma regressão — é uma limitação de isolamento
da suíte de testes, não do código do conversor (confirmado comparando a
mesma suíte com e sem o Docling instalado).
