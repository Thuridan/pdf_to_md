# Código — design do motor de conversão (`pdf_to_md.py`)

Design detalhado do módulo único que é a fonte de verdade de todo o projeto:
`pdf_to_md.py`. Ele é ao mesmo tempo a CLI e a biblioteca importada pelo
backend web ([`backend.md`](backend.md)) — não existem dois motores, um "para
a CLI" e outro "para a API". Para o porquê dessa escolha, ver
[`architecture.md`](architecture.md).

## Organização do módulo

O arquivo é um único módulo (`py-modules = ["pdf_to_md"]` no
`pyproject.toml`, não um pacote) dividido em seções comentadas, na ordem em
que os dados fluem:

```
Config (dataclass)              → parâmetros de conversão
aplicar_ambiente                → env vars, antes de qualquer import pesado
MotorBase / MotorDocling / MotorSimples → estratégia de conversão
selecionar_motor                → escolhe motor conforme Config + o que está instalado
parece_diretorio / resolver_saida / coletar_pdfs → resolução de caminhos
Resultado (dataclass)           → contrato de saída de uma conversão
contar_paginas                  → pré-checagem barata (pypdfium2)
escrever_atomico                → grava .md sem deixar arquivo truncado
converter_arquivo               → orquestra uma conversão de ponta a ponta
executar                        → orquestra um lote (sequencial ou paralelo)
CLI (argparse)                  → construir_parser, main
```

## `Config` — parâmetros desacoplados do `argparse`

```python
@dataclass
class Config:
    engine: str = "auto"
    ocr: bool = True
    ocr_engine: str = "rapidocr"
    ocr_backend: str = "onnxruntime"
    lang: list[str] = field(default_factory=lambda: ["pt"])
    tables: str = "accurate"
    threads: int = 4
    device: str = "auto"
    offline: bool = False
    artifacts: Path | None = None
    timeout: float | None = None
    overwrite: bool = False
    dry_run: bool = False
    max_pages: int | None = None
    jobs: int = 1
```

`Config` existe separado do `Namespace` do `argparse` por um motivo
específico: **é o que o backend web constrói diretamente**
(`motor_pool.inicializar()` faz `Config()` com os padrões), sem nunca passar
por linha de comando. Se os parâmetros de conversão estivessem amarrados ao
parser, reutilizar a lógica de motor a partir de outro processo de entrada
(a API) exigiria simular argumentos de CLI. Separar os dois também é o que
torna `Config` trivialmente testável — os testes constroem instâncias
diretamente, sem invocar `main()`.

## Por que os imports do Docling são preguiçosos

```python
def aplicar_ambiente(cfg: Config) -> None:
    """Define variaveis de ambiente ANTES de qualquer import de torch/onnx.
    As bibliotecas de tensores leem essas variaveis apenas no momento do import."""
    n = str(max(1, cfg.threads))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[var] = n
    ...
    if "torch" in sys.modules:
        LOG.warning("torch ja estava importado; limite de %s threads pode nao ter efeito.", n)
```

Isso dita uma regra de arquitetura que se propaga por todo o resto do
código: **nada pode `import docling`/`import torch` no topo do módulo**.
`OMP_NUM_THREADS` e afins só têm efeito se estiverem definidos *antes* de
torch/OpenBLAS serem carregados no processo — depois disso, a variável de
ambiente é ignorada silenciosamente. Por isso:

- `MotorDocling` importa `docling.*` só dentro dos métodos (`_opcoes_ocr`,
  `_opcoes_pipeline`, `_obter_converter`), nunca no topo do arquivo.
- `main()` chama `aplicar_ambiente(cfg)` antes de `executar()`.
- `motor_pool.inicializar()` no backend replica exatamente essa ordem:
  `aplicar_ambiente(_cfg)` antes de `selecionar_motor(_cfg)`.
- O próprio `aplicar_ambiente` verifica `"torch" in sys.modules` e avisa se
  alguém já quebrou essa ordem — um guard-rail para detectar regressões
  cedo, já que o efeito de esquecer isso é silencioso (threads não
  limitadas, sem erro visível).

## Estratégia de motores (`MotorBase`)

```python
class MotorBase:
    nome = "base"
    def disponivel(self) -> tuple[bool, str]: ...
    def converter(self, pdf: Path) -> str: ...
```

Interface mínima de duas operações: `disponivel()` (checagem barata, sem
importar a lib pesada — usa `importlib.util.find_spec`) e `converter()` (o
trabalho de fato). Duas implementações:

### `MotorDocling` — alta fidelidade

- **`_convs` é `{}` até a primeira conversão de cada modo** (rodada 3,
  TAREFA-4 — antes era um único `_conv`, ver `architecture.md`/`backend.md`
  para o motivo da troca: `do_ocr` é opção de construção do
  `DocumentConverter`, não argumento de `convert()`, então ligar/desligar
  OCR por job exige um converter por modo). `_obter_converter(ocr)`
  instancia um `DocumentConverter` sob demanda por chave (`True`/`False`) e
  cacheia em `self._convs[ocr]` — o carregamento de modelos (layout +
  TableFormer, e OCR só quando `ocr=True`) só acontece uma vez por
  combinação (instância de `MotorDocling`, modo de OCR), não por chamada de
  `converter()`. É essa memoização, combinada com o motor sendo
  reaproveitado por todo o lote (CLI) ou pela vida do processo (backend, via
  `motor_pool`), que evita recarregar modelos pesados repetidamente.
  `converter(pdf, *, ocr=None)` usa `self.cfg.ocr` quando `ocr` não é
  informado (compatibilidade com quem chama sem esse argumento) ou o valor
  explícito quando é — é assim que `converter_arquivo()` repassa o `cfg.ocr`
  por job (rodada 3) até o motor de fato.
- **`_opcoes_ocr()`** traduz `cfg.ocr_engine` para a classe de opções certa
  do Docling (`RapidOcrOptions`/`EasyOcrOptions`/`TesseractOcrOptions`).
  RapidOCR só aceita um idioma por execução — se `cfg.lang` tiver mais de
  um, o primeiro é usado e um aviso é logado (não é um erro, porque a CLI
  aceita `--lang pt en` para os outros motores de OCR sem restringir).
  Tesseract usa códigos ISO 639-2 (`por`, não `pt`) — `_MAPA_TESSERACT` faz
  essa tradução; um idioma sem entrada no mapa passa direto (deixa o
  Tesseract reclamar, se for inválido).
- **`_opcoes_pipeline()`** tem um fallback de compatibilidade: versões mais
  antigas do Docling tipavam `device` como um enum (`AcceleratorDevice.CPU`
  etc.), versões novas aceitam a string crua (`"cpu"`, `"cuda:0"`). O código
  tenta a forma nova primeiro; se falhar, cai para o enum, validando que o
  dispositivo pedido existe naquela versão antes de seguir.
- **`converter()`** inspeciona `resultado.status` do Docling: `failure`/
  `skipped` viram `ErroConversao` (com até 3 mensagens de erro do Docling
  agregadas); `partial_success` gera um aviso mas segue adiante (parte do
  conteúdo pode faltar, mas ainda é usável). Só então
  `resultado.document.export_to_markdown()` produz a string final.

### `MotorSimples` — leve, sem IA

```python
def converter(self, pdf: Path, *, ocr: bool | None = None) -> str:
    doc = pdfium.PdfDocument(str(pdf))
    ...
    texto = pagina.get_textpage().get_text_bounded() or ""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").strip()
```

Só lê a camada de texto nativa do PDF via `pypdfium2` — sem OCR, sem
detecção de tabelas, sem modelo nenhum carregado. O parâmetro `ocr` existe
só para casar a assinatura com `MotorBase`/`MotorDocling` (rodada 3,
TAREFA-4) — é ignorado, este motor nunca faz OCR de qualquer jeito. Páginas
concatenadas com
separador `\n\n---\n\n` (um único documento Markdown, não um por página). Se
nenhuma página tiver texto extraível (PDF puramente escaneado), levanta
`ErroConversao` com uma dica explícita para usar `--engine docling --ocr` —
o erro já aponta o caminho de resolução, não só o problema.

## `selecionar_motor` — fallback automático

```python
if cfg.engine == "auto":
    ok, motivo = docling.disponivel()
    if ok: return docling
    ok_s, motivo_s = simples.disponivel()
    if ok_s:
        LOG.warning("Docling indisponivel (...); usando motor 'simples' (sem OCR/tabelas).")
        return simples
    raise ErroConversao(...)
```

`--engine docling` ou `--engine simples` são pedidos explícitos: se o motor
pedido não estiver instalado, falha imediatamente com instrução de
instalação (`pip install 'docling[rapidocr]'` / `pip install pypdfium2`).
`--engine auto` (o padrão) tenta Docling primeiro e cai para `simples` só se
Docling realmente não estiver disponível — nunca ao contrário, porque
Docling é estritamente mais capaz (superset de `simples`). O aviso de
fallback é logado, não silencioso, porque a saída é materialmente diferente
(sem tabelas reconstruídas, sem OCR) e o usuário precisa saber por que a
qualidade caiu.

## Resolução de caminhos

### `parece_diretorio` — heurística

```python
def parece_diretorio(destino) -> bool:
    if p.is_dir(): return True
    if p.is_file(): return False
    if texto.endswith(("/", "\\", os.sep)): return True
    return p.suffix.lower() not in SUFIXOS_MD
```

Existe porque `-o saida/docs` (caminho ainda inexistente) precisa ser
tratado como diretório, não como um arquivo sem extensão chamado `docs` — a
versão anterior desse código cometia esse erro. A ordem de checagem importa:
existência real no disco vence qualquer heurística de nome; só quando o
caminho não existe ainda é que a extensão (`.md`/`.markdown` vs. qualquer
outra coisa) decide.

### `resolver_saida` — três modos

| Situação | Resultado |
|---|---|
| sem `-o` | `.md` ao lado do PDF de entrada |
| `-o` é diretório (ou parece um, via `parece_diretorio`) | preserva a subárvore relativa a `base` (o diretório de entrada original) — assim `-r` não achata a hierarquia de pastas |
| `-o` é um arquivo específico | só permitido quando há uma única entrada (`multiplas=False`); combinar com múltiplos PDFs é erro de uso |

### `coletar_pdfs` — expansão com deduplicação

Expande arquivos e diretórios (com `**/*` se `recursivo`) em uma lista de
`(pdf, diretorio_base)`, deduplicando pelo caminho resolvido (`Path.resolve()`)
— evita processar o mesmo PDF duas vezes se ele for alcançável por dois
argumentos de entrada diferentes (ex.: um diretório e um arquivo dentro
dele). Erros de coleta (diretório sem PDFs, caminho inexistente, arquivo
não-PDF) viram entradas em uma lista `problemas`, não exceções — o lote
inteiro roda com o que pôde ser coletado, e os problemas são reportados no
final.

## `converter_arquivo` — contrato de uma conversão

```python
def converter_arquivo(pdf, saida, motor, cfg) -> Resultado:
    if saida.resolve() == pdf.resolve():
        return Resultado(pdf, saida, "erro", "saida coincide com a entrada")
    if saida.exists() and not cfg.overwrite:
        return Resultado(pdf, saida, "pulado", "ja existe (use --overwrite)")
    if cfg.dry_run:
        return Resultado(pdf, saida, "ok", "simulado (--dry-run)")
    if cfg.max_pages is not None:
        ...  # pré-checagem barata via contar_paginas, falha cedo se exceder
    ...
    markdown = motor.converter(pdf)   # única chamada que pode ser lenta/pesada
    ...
    escrever_atomico(saida, markdown)
    return Resultado(pdf, saida, "ok", "", segundos, len(markdown))
```

Essa função **nunca levanta exceção para o chamador** — toda falha
(conflito de caminho, arquivo já existe, PDF corrompido, conteúdo vazio,
falha de escrita em disco) vira um `Resultado` com `status="erro"` e uma
mensagem legível. É esse contrato que permite ao backend web rodar
conversões dentro de uma thread worker sem precisar de um `try/except`
genérico ao redor de cada chamada — `jobs._processar()` só olha
`resultado.status`. As exceções capturadas incluem tanto `ErroConversao`
(esperada, do motor) quanto `Exception` genérica (defensivo, para bugs não
previstos no motor não derrubarem o lote inteiro).

A checagem de `--max-pages` acontece **antes** de chamar `motor.converter()`
— é uma pré-checagem barata (`contar_paginas`, via `pypdfium2`, sem carregar
nenhum modelo) que existe justamente para rejeitar PDFs gigantes/adversariais
sem gastar minutos de OCR/layout primeiro.

## `escrever_atomico` — sem `.md` truncado

```python
def escrever_atomico(destino: Path, conteudo: str) -> None:
    temporario = destino.with_name(destino.name + ".tmp")
    try:
        temporario.write_text(conteudo, encoding="utf-8")
        temporario.replace(destino)   # rename atômico no mesmo filesystem
    finally:
        if temporario.exists():
            temporario.unlink(missing_ok=True)
```

Grava em um arquivo `.tmp` e só substitui o destino final com `rename` (que
é atômico no mesmo filesystem) — se o processo morrer no meio da escrita
(disco cheio, kill -9), o pior caso é um `.tmp` órfão, nunca um `.md` de
saída pela metade sendo confundido com um resultado válido.

## `executar` — orquestração de lote

Antes de converter qualquer coisa, `executar()` calcula **todos** os
caminhos de saída planejados e detecta colisões (dois PDFs diferentes que
gerariam o mesmo `.md`) usando `os.path.normcase` para comparação
case-insensitive-aware por plataforma. Se houver colisão, o lote inteiro é
abortado antes de processar o primeiro arquivo — evita que o segundo PDF
processado sobrescreva silenciosamente o resultado do primeiro.

### Paralelismo de lote

```python
if jobs > 1 and motor.nome != "simples":
    LOG.warning("--jobs %d ignorado para o motor '%s' ...")
    jobs = 1
if jobs > 1 and len(planejado) > 1:
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
        resultados = list(executor.map(_converter_pool_item, itens))
```

`--jobs N` só tem efeito real com `--engine simples`: `MotorSimples` não tem
estado (`pypdfium2` é barato de reabrir por processo), então paralelizar via
`ProcessPoolExecutor` é puro ganho. Com o motor Docling, cada processo do
pool teria que recarregar o layout model + TableFormer + OCR inteiros —
trocaria RAM por paralelismo sem ganho real de throughput, já que a
instância única de `DocumentConverter` dentro de **um** processo já é o
desenho mais eficiente (ver [`architecture.md`](architecture.md#modelo-de-concorrência)).
`_converter_pool_item` existe como função de nível de módulo (não uma
closure/lambda) porque `ProcessPoolExecutor` precisa que a função passada a
`executor.map` seja "picklable" para ser enviada aos processos filhos.

## CLI (`argparse`)

`construir_parser()` define todas as flags documentadas no
[README](../README.md#parâmetros). `main()` faz validação de uso (device
inválido, threads/timeout/max-pages/jobs fora de faixa) **antes** de
construir `Config` e chamar `aplicar_ambiente` — erros de uso retornam
`EXIT_USO` (2) sem nunca chegar a importar nada pesado. Se `-i`/`--input` for
omitido, `perguntar_entrada()` cai para um modo interativo simples
(`input()`), tratando `Ctrl+C`/EOF como cancelamento (retorna `None`, não
propaga a exceção).

Códigos de saída: `0` sucesso · `1` alguma falha (incluindo lote com erros
parciais) · `2` erro de uso · `130` interrompido (`KeyboardInterrupt`
capturado tanto dentro de `main()` quanto no `if __name__ == "__main__":`,
para cobrir interrupção antes e depois do parsing de argumentos).
