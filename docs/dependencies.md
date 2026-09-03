# Dependências — design de gerenciamento

Como o projeto declara, versiona e instala suas dependências, e por quê.
Para uma análise de cada biblioteca em si (o que ela faz, como o código a
usa), ver [`library.md`](library.md).

## Requisito de Python

`requires-python = ">=3.10"` (`pyproject.toml`) — o código usa
`from __future__ import annotations` em todos os módulos (permite anotações
como `Path | None` sem precisar de `Optional`/`Union` explícitos mesmo antes
de serem nativamente lazy) e sintaxe de union com `|` em tipos, disponível a
partir do 3.10.

## Extras: instalação por caso de uso

O projeto não tem um único `pip install .` que traga tudo — as dependências
pesadas (Docling, FastAPI) são opt-in via extras, porque nem todo uso
precisa de todas elas:

```toml
[project.optional-dependencies]
simples = ["pypdfium2>=4,<6"]
docling = ["docling[rapidocr]>=2.100,<3"]
dev     = ["fpdf2>=2.7,<3"]
web     = ["fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1", "python-multipart>=0.0.9,<1"]
```

| Extra | Quando instalar | O que traz |
|---|---|---|
| `simples` | só quer extração de texto nativo, sem IA/OCR | `pypdfium2` |
| `docling` | quer o motor de alta fidelidade (padrão) | `docling[rapidocr]` — traz `torch`, `onnxruntime`, `transformers` e dezenas de dependências transitivas |
| `dev` | vai rodar a suíte de testes | `fpdf2`, para gerar PDFs sintéticos nos testes (ver [`library.md`](library.md#fpdf2)) |
| `web` | vai rodar a aplicação FastAPI | `fastapi`, `uvicorn[standard]`, `python-multipart` |

Combinações típicas: `pip install ".[docling]"` (CLI completa),
`pip install ".[docling,web]"` (app web com motor de alta fidelidade),
`pip install ".[docling,dev]"` (para rodar `test_pdf_to_md.py` e
`backend/tests/`).

Instalação alternativa sem clonar o `pyproject.toml` (ex.: em outro
projeto que só quer importar o módulo): `pip install "docling[rapidocr]"` e/ou
`pip install pypdfium2` diretamente — os extras do projeto só encapsulam
essas mesmas faixas de versão.

## Filosofia de versionamento: faixas, não pins exatos

```toml
docling = ["docling[rapidocr]>=2.100,<3"]
fastapi = "fastapi>=0.115,<1"
```

Teto de major version, não uma versão exata (`==2.100.0`). Motivo: o Docling
em particular evolui rápido (breaking changes já aconteceram entre minors —
ver o fallback de compatibilidade de `AcceleratorDevice`/`AcceleratorOptions`
em [`code.md`](code.md#motordocling--alta-fidelidade)), então fixar uma
única versão travaria o projeto numa versão desatualizada rapidamente; ao
mesmo tempo, deixar o pip resolver "o que for mais recente" sem nenhum teto
arrisca quebrar em uma mudança de major version sem aviso. A faixa é o meio
termo: aceita patches e minors dentro do major testado.

## Por que não hash-pin (`pip install --require-hashes`)

Pinar cada arquivo por hash (`pip freeze --require-hashes`) daria
reprodutibilidade byte-a-byte, mas o Docling (via `torch`/`onnxruntime`) traz
**wheels diferentes por plataforma e acelerador** (CPU vs. CUDA vs. MPS vs.
XPU) — uma lista de hashes gerada em uma máquina só cobre a combinação
daquela máquina. Hash-pinning aqui trocaria reprodutibilidade de plataforma
única por instalação quebrada em qualquer outra.

## `requirements-lock.txt` — retrato, não caminho de instalação

```
accelerate==1.14.0
docling==2.123.1
numpy==2.5.2
onnxruntime==1.29.0
opencv-python-headless==5.0.0.93
torch==2.13.0
...
```

Gerado com `pip freeze` a partir de uma instalação completa testada
ponta-a-ponta (127 pacotes, incluindo toda a árvore transitiva do Docling:
`torch`, `torchvision`, `transformers`, `accelerate`, os pacotes `nvidia-*`
de CUDA, `rapidocr`, etc.). Serve para **reproduzir exatamente** o ambiente
testado numa máquina com a mesma plataforma — não é o caminho de instalação
recomendado para um projeto novo (que deve usar os extras do
`pyproject.toml` e deixar o pip resolver a faixa declarada).

Duas particularidades documentadas nesse arquivo:

- **`opencv-python-headless` no lugar de `opencv-python`.** `rapidocr`
  depende de `opencv-python` (que traz bindings gráficos/`libGL`); em Linux
  sem ambiente gráfico isso falha com
  `ImportError: libGL.so.1: cannot open shared object file`. A correção sem
  acesso root é desinstalar `opencv-python` e instalar
  `opencv-python-headless`, que expõe o mesmo módulo `cv2` sem a dependência
  gráfica. O lock captura esse estado já corrigido.
- **Faixa de pacotes NVIDIA (`nvidia-cublas`, `nvidia-cudnn-cu13`, etc.)**
  fazem parte da árvore de dependências do `torch` com suporte a CUDA — vêm
  automaticamente ao instalar `torch` com essa configuração, não são
  declarados diretamente pelo projeto.

## Variáveis de ambiente consumidas

| Variável | Definida por | Efeito |
|---|---|---|
| `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMEXPR_NUM_THREADS` | `aplicar_ambiente()`, a partir de `Config.threads` | Limita threads de CPU usadas pelas libs de álgebra linear/tensores — só tem efeito se definidas **antes** do primeiro import de `torch`/OpenBLAS no processo. |
| `TOKENIZERS_PARALLELISM` | `aplicar_ambiente()` (sempre `"false"`, via `setdefault`) | Evita o aviso de fork do tokenizers do Hugging Face. |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `aplicar_ambiente()`, só se `Config.offline=True` | Bloqueia o Hugging Face Hub de checar atualizações de modelo (layout/TableFormer). Não afeta o RapidOCR, cujos modelos vêm do ModelScope. |
| `HOST`, `PORT` | lidas por `start.sh`/`restart.sh` (não pelo Python) | Endereço/porta do uvicorn ao subir a aplicação web; padrão `0.0.0.0:8000` (todas as interfaces). |
| `MAX_UPLOAD_BYTES` | `backend/src/routes/api.py`, lida no import do módulo | Teto de tamanho por arquivo em `POST /api/jobs`; acima disso o arquivo vai para `rejeitados` sem ser gravado em disco. Padrão `104857600` (100 MiB). |
| `MAX_UPLOAD_FILES` | `backend/src/routes/api.py`, lida no import do módulo | Teto de quantidade de arquivos aceitos por lote em `POST /api/jobs`; o excedente vai para `rejeitados`. Padrão `50`. |
| `MAX_UPLOAD_PAGES` | `backend/src/services/motor_pool.py`, lida no import do módulo | Vira `Config.max_pages` do motor da aplicação web (a CLI não usa esta variável — lá quem decide é `--max-pages`, desligado por padrão). Padrão `500`. |

## Entry point

```toml
[project.scripts]
pdf-to-md = "pdf_to_md:main"
```

Instalar o pacote (`pip install .`) registra o comando `pdf-to-md` no
`PATH` do ambiente virtual, invocando `pdf_to_md.main()` diretamente — a
mesma função chamada por `python pdf_to_md.py` via
`if __name__ == "__main__":`.

## Dependências do frontend

Nenhuma. `frontend/` não tem `package.json` — ver
[`frontend.md`](frontend.md#decisão-fundamental-sem-build-step) para o
raciocínio completo. O único recurso externo é a folha de estilo de fontes
do Google Fonts, carregada por `<link>` no HTML (sem SDK/JS de terceiros).
