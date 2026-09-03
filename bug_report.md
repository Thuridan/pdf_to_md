# bug_report.md — execução do relatório `bugs.md`

## 1. Cabeçalho

- **Data de execução:** 2026-09-02
- **Commit inicial:** `a7f3fc6` (Fix docs cross-links left stale by the docs/ move — estado do repo antes de qualquer correção deste relatório)
- **Commit final:** `a42ecc5` (Fix three documentation inaccuracies flagged alongside the bug review)
- **Sistema operacional:** Ubuntu 24.04.4 LTS (x86_64)
- **Ambiente de validação** (mesmo ambiente para todos os itens, salvo nota em contrário):
  - Python 3.12.3
  - `docling` 2.123.1
  - `pypdfium2` 5.13.0
  - `fastapi` 0.141.1
  - `starlette` 1.6.0
  - `setuptools` 84.0.0 (instalado; o *floor* declarado em `pyproject.toml` após BUG-07 é `>=77`)
  - `uvicorn` 0.52.4
  - `httpx2` 2.12.0 (adicionado ao extra `dev` pelo BUG-16)

**Notas de plataforma:**
- **BUG-05** (zip-slip via `\` no Windows): o servidor roda em Linux (Ubuntu 24.04.4, acima); o bug e a correção foram validados por inspeção direta do valor computado do `arcname`/`Content-Disposition` (`Path(...).name` não trata `\` como separador em POSIX), não por extrair um `.zip` de verdade num cliente Windows — não há Windows disponível neste ambiente.
- **BUG-07** (`setuptools>=68` falha com PEP 639): reproduzido e verificado em venvs limpos nesta mesma máquina Ubuntu 24.04.4/Python 3.12.3, com `setuptools==68.2.2` (falha) e `setuptools==77.0.1` (sucesso — `77.0.0` exato não está publicado no PyPI, usada a versão `>=77` mais próxima disponível).

---

## 2. Quadro-resumo

| ID | Prioridade | Estado | Corrigido | Commit | Testes adicionados |
|---|---|---|---|---|---|
| BUG-01 | P0 | VÁLIDO (reproduzido) | sim | `cd5b55d` | 1 |
| BUG-02 | P0 | VÁLIDO (reproduzido) | sim | `e7d339a` | 2 |
| BUG-03 | P0 | VÁLIDO (reproduzido) | sim | `a66c484` | 2 |
| BUG-04 | P0 | VÁLIDO (reproduzido) | sim | `b45278b` | 1 (líquido: +2, -1 renomeado) |
| BUG-05 | P0 | VÁLIDO (reproduzido) | sim | `643af5d` | 5 |
| BUG-06 | P1 | VÁLIDO (confirmado por inspeção) | sim | `e67b6b4` | 1 |
| BUG-07 | P1 | VÁLIDO (reproduzido) | sim | `2ae0f62` | 0 |
| BUG-08 | P1 | VÁLIDO (reproduzido) | sim (documentado) | `7e728da` | 0 |
| BUG-09 | P1 | VÁLIDO (confirmado por inspeção) | sim | `92f43da` | 0 |
| BUG-10 | P1 | VÁLIDO (confirmado por inspeção) | **parcial** (decisão de produto) | `b62fc63` | 0 |
| BUG-11 | P2 | VÁLIDO (não alcançável hoje) [ver Errata em bug_report-2.md] | sim | `201719f` | 1 |
| BUG-12 | P2 | VÁLIDO (reproduzido) | sim | `891ee97` | 4 |
| BUG-13 | P2 | VÁLIDO (reproduzido) | sim | `fbb43a4` | 2 |
| BUG-14 | P2 | VÁLIDO (confirmado por inspeção) | sim | `bf6d207` | 0 |
| BUG-15 | P2 | VÁLIDO (reproduzido) | sim | `a4f71b6` | 2 |
| BUG-16 | P2 | VÁLIDO (reproduzido) | sim | `4ec2685` | 1 |
| BUG-17 | P3 | VÁLIDO (confirmado por inspeção) | sim | `7cd1803` | 1 |
| BUG-18 | P3 | VÁLIDO (confirmado por inspeção) | sim | `7ae1bcd` | 1 |
| BUG-19 | P3 | VÁLIDO (reproduzido) | sim | `6df1a45` | 1 |
| BUG-20 | P3 | VÁLIDO (confirmado por inspeção) | sim | `25fd501` | 1 |
| BUG-21 | P3 | VÁLIDO (não alcançável hoje) [ver Errata em bug_report-2.md] | sim | `13a5fdc` | 1 |
| BUG-22 | P3 | VÁLIDO (reproduzido) | sim | `c90a767` | 2 |
| BUG-23 | P3 | VÁLIDO (confirmado por inspeção) | sim | `13cd9ff` | 1 |

**Contagens (corrigidas por errata — ver `bug_report-2.md`, BUG-26):**
- Válidos (reproduzidos): ~~15~~ **13**
- Válidos (confirmados por inspeção): 8
- Válidos (não alcançáveis hoje): **2** (BUG-11, BUG-21 — reclassificados; ver `bug_report-2.md`)
- Inválidos: 0
- Corrigidos: 23 de 23 (22 integralmente; BUG-10 parcialmente — ver Pendências)
- Total de testes adicionados: **30** (11 em `test_pdf_to_md.py`, 19 em `backend/tests/`)

---

## 3. Detalhe por item

### BUG-01 — Worker morre em silêncio e a fila para para sempre

- **Estado:** VÁLIDO (reproduzido) · P0
- **Validação:** script que enfileira 4 jobs reais via `jobs.criar_job`/`jobs.enfileirar`, com `motor_pool.obter_motor()` monkeypatchado pra levantar `RuntimeError` na 2ª chamada. Resultado: `job-0: concluido`, `job-1: processando` (preso), `job-2`/`job-3: na_fila`, `jobs._worker_thread.is_alive() == False`.
- **Correção aplicada:** `backend/src/services/jobs.py` — `_loop()` ganhou `except Exception` em volta de `_processar()`, logando e chamando um novo `_marcar_erro(job_id, mensagem)` que marca o job como `"erro"` em vez de deixá-lo preso. Adicionado `LOG = logging.getLogger(__name__)` (o módulo não tinha logger).
- **Validação da correção:** mesmo script — `job-1: erro`, `job-2`/`job-3: concluido`, worker `is_alive() == True`.
- **Testes de regressão:** `TestFilaSobreviveAErroInesperado.test_falha_inesperada_no_meio_da_fila_nao_derruba_o_worker` (`backend/tests/test_jobs.py`).
- **Risco residual:** `except Exception` não captura `BaseException` (`SystemExit`/`KeyboardInterrupt`/etc.) — comportamento deliberado, não deveria capturar esses.

### BUG-02 — Aplicação não sobe sem nenhum motor instalado

- **Estado:** VÁLIDO (reproduzido) · P0
- **Validação:** venv limpo, `pip install -e ".[web]"` (sem `docling`/`simples`), depois `uvicorn backend.src.app:app` → `pdf_to_md.ErroConversao: Nenhum motor disponivel (...)`, `Application startup failed. Exiting.`
- **Correção aplicada:** `pyproject.toml` — `pypdfium2>=4.30.0,!=4.30.1,<6` virou dependência obrigatória em `[project.dependencies]` (a exclusão `!=4.30.1` replica a que o próprio `docling-slim` declara — confirmado baixando `docling_slim` 2.123.1 e inspecionando seu `METADATA`). Extra `web` passou a depender de `pdf-to-md[simples]`.
- **Validação da correção:** venv limpo, `pip install -e ".[web]"`, `uvicorn backend.src.app:app` sobe, loga "Docling indisponivel ... usando motor 'simples'", `GET /api/health` e `GET /api/motor` respondem `200`.
- **Testes de regressão:** `TestEmpacotamento.test_pypdfium2_e_dependencia_obrigatoria`, `test_extra_web_garante_um_motor_disponivel` (`test_pdf_to_md.py`).
- **Risco residual:** nenhum — o motor `simples` é estritamente menos capaz que `docling` (sem OCR/tabelas), mas isso é o comportamento documentado de fallback, não uma lacuna desta correção.

### BUG-03 — Superfície web sem limite de entrada

- **Estado:** VÁLIDO (reproduzido) · P0
- **Validação:** chamada direta a `api.criar_jobs()` com 60 arquivos minúsculos (todos aceitos, sem teto de lote) e com um arquivo de 3 MiB (aceito integralmente, sem teto de tamanho).
- **Correção aplicada:** `backend/src/services/motor_pool.py` — `inicializar()` usa `Config(max_pages=_MAX_PAGINAS_PADRAO)` por padrão (`MAX_UPLOAD_PAGES`, padrão 500). `backend/src/routes/api.py` — novas `MAX_UPLOAD_BYTES` (padrão 100 MiB) e `MAX_UPLOAD_ARQUIVOS` (padrão 50), lidas de variável de ambiente; novo helper `_ler_com_teto()` lê em blocos de 1 MiB em vez de `arquivo.read()` de uma vez, abortando cedo se ultrapassar o teto. Arquivos acima do teto (de tamanho ou de contagem) vão para `rejeitados`, mantendo o contrato `200` da rota.
- **Validação da correção:** com tetos pequenos sobrepostos via monkeypatch (`MAX_UPLOAD_ARQUIVOS=5`, `MAX_UPLOAD_BYTES=1024`), lote de 8 arquivos aceita só 5, arquivo de 2 KiB é rejeitado, nenhum arquivo acima do teto é gravado em disco.
- **Testes de regressão:** `TestLimitesDeUpload.test_arquivo_acima_do_teto_de_tamanho_e_rejeitado_sem_gravar_nada`, `test_lote_acima_do_teto_de_quantidade_rejeita_o_excedente` (`backend/tests/test_app.py`).
- **Documentação:** `MAX_UPLOAD_BYTES`, `MAX_UPLOAD_FILES`, `MAX_UPLOAD_PAGES` documentados na tabela de variáveis de ambiente de `dependencies.md`.
- **Risco residual:** os padrões (100 MiB/50 arquivos/500 páginas) são arbitrários — ajustáveis por variável de ambiente conforme o deployment.

### BUG-04 — `--max-pages` ignorado silenciosamente em PDF ilegível

- **Estado:** VÁLIDO (reproduzido) · P0
- **Validação:** `Config(max_pages=10)` + arquivo `b"%PDF-1.4 lixo"` + motor stub que registra chamadas → `motor.chamado == True` mesmo com `--max-pages` ativo (deveria ter sido bloqueado antes de chegar ao motor).
- **Correção aplicada:** `pdf_to_md.py` — nova exceção `PdfIlegivel(RuntimeError)`; `contar_paginas()` agora levanta `PdfIlegivel` quando o `pypdfium2` está instalado mas não consegue abrir o arquivo (antes devolvia `None` nos dois casos — "biblioteca ausente" *e* "PDF ilegível" — indistinguíveis). `converter_arquivo()` captura `PdfIlegivel` e rejeita com `"nao foi possivel pre-checar o numero de paginas"` quando `max_pages` está ativo. `backend/src/services/jobs.py` — `criar_job()` (que também chama `contar_paginas()`, mas só pra estimativa de progresso, não pra segurança) captura `PdfIlegivel` e mantém o fallback antigo (`paginas_totais = None`), preservando o comportamento existente lá.
- **Validação da correção:** mesmo script — `motor.chamado == False`, `resultado.status == "erro"`, mensagem `"nao foi possivel pre-checar o numero de paginas"`.
- **Testes de regressão:** `TestMaxPages.test_pdf_corrompido_com_max_pages_ativo_e_bloqueado_pela_pre_checagem` (novo) e `test_pdf_corrompido_sem_max_pages_ainda_falha_por_conta_do_motor` (renomeado de `test_pdf_corrompido_ainda_falha_por_conta_do_motor`, que descrevia o comportamento *antigo* como intencional em seu próprio docstring) em `test_pdf_to_md.py`. `backend/tests/test_jobs.py::test_pdf_invalido_deixa_paginas_totais_none` (pré-existente) confirma que o call site de `jobs.py` não regrediu.
- **Dependência:** depende do BUG-02 (pypdfium2 obrigatório) — sem isso, "biblioteca ausente" ainda seria o caminho normal de instalação leve, não um caso de erro.
- **Risco residual:** nenhum além do já documentado por `--max-pages`: um PDF ilegível pelo pypdfium2 mas processável pelo Docling agora é rejeitado sob `--max-pages`, que é exatamente a proteção que a flag existe para dar.

### BUG-05 — Zip-slip via separador do Windows

- **Estado:** VÁLIDO (reproduzido) · P0
- **Validação:** `Path("..\\..\\..\\Windows\\System32\\evil.pdf").with_suffix(".md").name` → `'..\\..\\..\\Windows\\System32\\evil.md'` (backslashes preservados intactos); confirmado que esse valor seria gravado literalmente como `arcname` do zip e como `filename` do `Content-Disposition`.
- **Correção aplicada:** `backend/src/routes/api.py` — novo helper `_nome_saida_seguro(nome_original, job_id)`, usado em `baixar_job()` e `baixar_zip()`. Normaliza `\` para `/` antes de `Path(...).name` (neutraliza os dois estilos de separador) e troca a extensão via string, **não** via `Path.with_suffix()`.
- **Divergência da proposta original:** a correção sugerida em `bugs.md` usava `Path(bruto).with_suffix(".md").name`. Testando os próprios casos de verificação pedidos (`""`, `"."`, `".."`), essa versão levanta `ValueError: PosixPath('.') has an empty name` — um `500` não tratado, justo num dos casos que a verificação pede pra cobrir. Implementada a versão sem `with_suffix()` (troca de extensão via `rsplit`), que não levanta em nenhum dos casos testados.
- **Validação da correção:** os 5 casos pedidos (`..\..\evil.pdf`, `../../etc/passwd.pdf`, `/etc/x.pdf`, `...pdf`, `""`) mais `.`, `..`, `\` isolados — todos devolvem um nome sem `/`, `\` ou `..`, nunca vazio, nenhum levanta exceção.
- **Testes de regressão:** `TestNomeSaidaSeguro` (3 testes, unitários no helper) + `TestDownloadEndpoints.test_zip_nao_contem_entrada_com_separador_windows_no_arcname` + `test_download_de_job_com_nome_windows_nao_leva_separador_ao_content_disposition` (ponta a ponta via `TestClient`) — 5 no total, em `backend/tests/test_app.py`.
- **Risco residual:** nenhum identificado dentro do escopo (nomes de saída derivados de upload).

### BUG-06 — Ordem de atribuição expõe job "concluído" sem arquivo de saída

- **Estado:** VÁLIDO (confirmado por inspeção) · P1 — corrida entre threads, `bugs.md` pede explicitamente para não forçar reprodução com `sleep`/instrumentação em código de produção.
- **Validação:** leitura de `_processar()` (`backend/src/services/jobs.py`, então nas linhas ~220-234): `job.status = "processando"` antes de `job.iniciado_em = ...`; e `job.status = "concluido"` antes de `job.caminho_saida = ...`. Uma thread HTTP lendo o `Job` nesse intervalo observaria o novo status com o dado que ele implica ainda `None`.
- **Correção aplicada:** inverte a ordem dos dois pares — o dado é sempre atribuído antes do status que o anuncia.
- **Validação da correção:** revisão do diff (a inversão é textualmente direta); teste de melhor esforço abaixo.
- **Testes de regressão:** `TestOrdemDeAtribuicaoDoJob.test_status_nunca_e_observado_antes_do_dado_correspondente` — usa o atraso já existente do motor stub de teste (não introduz `sleep` em código de produção) pra dar múltiplas janelas de poll durante um processamento real; não garante disparar a janela em toda execução, mas adiciona sinal real.
- **Risco residual:** a garantia de atomicidade continua sendo "atribuição de atributo único é atômica em CPython", não um lock explícito — documentado com mais precisão em `architecture.md` (ver seção 5).

### BUG-07 — Build falha na versão mínima de setuptools declarada

- **Estado:** VÁLIDO (reproduzido) · P1
- **Validação:** venv limpo com `setuptools==68.2.2` + `python -m build --wheel --no-isolation` → `ValueError: invalid pyproject.toml config: 'project.license'. ... must be valid exactly by one definition (2 matches found)`.
- **Correção aplicada:** `pyproject.toml` — `[build-system] requires = ["setuptools>=77"]` (era `>=68`).
- **Validação da correção:** venv limpo com `setuptools==77.0.1` (77.0.0 exato não publicado no PyPI) + `python -m build --wheel --no-isolation` → wheel construído com sucesso.
- **Testes de regressão:** nenhum — exercitar isso exige instalar uma versão específica de `setuptools` e invocar o build backend num venv isolado, impraticável dentro das suítes rápidas de unit test; é uma correção de versão de packaging/tooling, não de lógica de aplicação.
- **Risco residual:** nenhuma versão exata é fixada (só um piso `>=77`) — uma futura major do setuptools que quebre algo continua possível, mas é o mesmo trade-off de faixas-não-pins já adotado no resto do projeto (ver `dependencies.md`).

### BUG-08 — Wheel não contém a aplicação web

- **Estado:** VÁLIDO (reproduzido) · P1
- **Validação:** `python -m build --wheel` e inspeção do `.whl` construído — só `pdf_to_md.py` e `dist-info`, sem `backend/` nem `frontend/`.
- **Correção aplicada:** opção (b) escolhida entre as duas propostas — **documentar** que o wheel cobre só a CLI, em vez de empacotar a webapp. A alternativa (a) proposta (`package-data = ["../frontend/*"]`) aponta pra fora do diretório do pacote, uso não-padrão do setuptools com risco de correção próprio; empacotar um servidor redistribuível também contradiz o escopo documentado do projeto (`architecture.md`: ferramenta local/single-tenant). Comentário do extra `web` em `pyproject.toml`, `dependencies.md` (nova subseção "O wheel cobre só a CLI") e a seção "Aplicação web" do `README.md` atualizados para deixar explícito que rodar o modo web exige `cd` dentro do repositório clonado — o que `scripts/start.sh` já assumia.
- **Validação da correção:** leitura cruzada dos três documentos + confirmação de que nenhum link interno quebrou.
- **Testes de regressão:** nenhum — mudança de documentação/escopo de packaging, não de lógica.
- **Risco residual:** nenhum — decisão de escopo documentada, não uma capacidade pendente.

### BUG-09 — Polls sobrepostos fazem a UI andar para trás

- **Estado:** VÁLIDO (confirmado por inspeção) · P1 — sem runtime Node/browser disponível neste ambiente para uma reprodução em DOM real, e sem suíte de testes JS no repositório (`frontend.md` documenta zero dependências npm como escolha deliberada).
- **Validação:** rastreamento manual de uma interleaving concreta: `setInterval(atualizarFila, POLL_MS)` não esperava a chamada anterior terminar; uma resposta lenta (t=0) sobreposta por uma resposta rápida de um segundo poll (t=1.5s) resolvendo primeiro, seguida da resposta lenta original chegando depois e sobrescrevendo `jobsCache` incondicionalmente — reproduz a regressão descrita contra o código pré-correção.
- **Correção aplicada:** `frontend/app.js` — número de sequência `seqAtual`, incrementado a cada chamada de `atualizarFila()`; uma resposta só é aplicada se seu carimbo ainda for o mais recente quando ela resolve. `setInterval` trocado por uma cadeia de `setTimeout` auto-reagendante (`agendar()`), então o poll periódico não se sobrepõe com sua própria chamada anterior — o guard de sequência sozinho ainda protege as chamadas de `enviarArquivos()`/`removerJob()`, que continuam fora dessa cadeia.
- **Validação da correção:** mesmo rastreamento, contra o código corrigido — a resposta lenta chega com `seq !== seqAtual` (já superada pela resposta rápida) e é descartada.
- **Testes de regressão:** nenhum (ver justificativa da classificação).
- **Risco residual:** a correção elimina a sobreposição do polling periódico consigo mesmo e a regressão de estado por resposta obsoleta; não foi validada em execução real de navegador.

### BUG-10 — Escuta em todas as interfaces por padrão, sem autenticação

- **Estado:** VÁLIDO (confirmado por inspeção) · P1
- **Corrigido:** **parcialmente** — decisão de produto, ver Pendências.
- **Validação:** `scripts/start.sh` tinha `HOST="${HOST:-0.0.0.0}"`; sem autenticação/HTTPS/rate limiting na camada web (confirmado em `backend/src/app.py`/`routes/api.py`).
- **Correção proposta em `bugs.md`:** reverter o padrão para `127.0.0.1`. **Não aplicada** — o padrão `0.0.0.0` foi uma decisão deliberada e recente (commit `8cd4860`, mais cedo nesta mesma sessão): o padrão anterior (`127.0.0.1`) tornava a aplicação inacessível de outras máquinas na rede local, o que parecia um problema de firewall mas não era. Reverter aqui desfaria silenciosamente essa correção. Perguntado ao usuário, que escolheu manter `0.0.0.0` e aplicar só a parte não controversa da correção.
- **Correção aplicada:** `scripts/start.sh` agora imprime um aviso visível em stderr sempre que `HOST` resolve para algo além de loopback (`127.0.0.1`/`localhost`/`::1`), nomeando a exposição e como restringi-la — silencioso só no caso loopback.
- **Validação da correção:** `PORT=8097 ./scripts/start.sh` (HOST=0.0.0.0 padrão) imprime o aviso; `HOST=127.0.0.1 PORT=8097 ./scripts/start.sh` não imprime nada.
- **Testes de regressão:** nenhum — mudança em script shell, não em código Python coberto pelas suítes.
- **Risco residual:** o padrão continua expondo a aplicação (sem auth/HTTPS) em todas as interfaces por padrão — mitigado só pelo aviso visível e pela orientação de usar firewall (`ufw`), não por uma mudança de comportamento.

### BUG-11 — `pulado` reportado ao usuário como `erro`

> **Errata (ver `bug_report-2.md`, BUG-26):** o estado abaixo foi
> reclassificado de `VÁLIDO (reproduzido)` para `VÁLIDO (não alcançável
> hoje)`. A validação chamou `_processar()` diretamente, de um jeito que
> nenhum caminho real da aplicação invoca — não há endpoint de
> retry/reenfileiramento hoje, então não é reprodução do bug em produção,
> é demonstração de comportamento fora do fluxo. O texto de validação
> abaixo já registrava isso corretamente; só a etiqueta estava errada.

- **Estado:** VÁLIDO (não alcançável hoje) · P2
- **Validação:** `jobs._processar(job.id)` chamado duas vezes seguidas pro mesmo job (simulando um reprocessamento hipotético) — 2ª chamada: `status == "erro"`, `mensagem_erro == "ja existe (use --overwrite)"` (mensagem que só faz sentido no contexto da CLI). Não há endpoint de retry/reenfileiramento na API pública hoje — o caminho só é alcançável chamando `_processar()` diretamente, o que é exatamente o que uma futura funcionalidade de retry (ou um bug de reenfileiramento duplicado) faria.
- **Correção aplicada:** `backend/src/services/jobs.py` — `_processar()` trata `resultado.status in ("ok", "pulado")` como sucesso (`status = "concluido"`), mas só chama `_atualizar_estimativa()` quando `resultado.status == "ok"` — um resultado `"pulado"` tem `resultado.segundos == 0.0`, e alimentar isso na média móvel corromperia a estimativa de progresso das conversões reais.
- **Validação da correção:** mesmo cenário — 2ª chamada: `status == "concluido"`, `mensagem_erro == ""`.
- **Testes de regressão:** `TestProcessarResultadoPulado.test_segunda_passada_com_saida_ja_existente_conta_como_concluido` (`backend/tests/test_jobs.py`).
- **Risco residual:** nenhum endpoint de retry existe hoje — a correção é defensiva/preparatória pra quando um existir (ou pra qualquer bug futuro que reenfileire o mesmo job_id).

### BUG-12 — TOCTOU em `remover_job`

- **Estado:** VÁLIDO (reproduzido) · P2
- **Validação:** com um atraso artificial introduzido *no script de teste* (não em código de produção) entre o check de status da rota e a chamada a `jobs.remover()`, mais um worker real: o PDF foi apagado e o job sumiu do `_store` enquanto `job.status` ainda era `"processando"` — confirmado por um `OSError` real de limpeza de diretório temporário colidindo com a thread órfã ainda escrevendo.
- **Correção aplicada:** `backend/src/services/jobs.py` — novo `JobStore.remover_se_nao_processando(job_id)`: checa o status e remove do dicionário sob o mesmo lock, atomicamente. `jobs.remover_se_nao_processando()` embrulha isso apagando os arquivos. `backend/src/routes/api.py` — `remover_job()` agora chama essa função primeiro; só faz uma segunda leitura (não-atômica) pra decidir entre `404`/`409` quando a remoção atômica já falhou — nunca pra decidir *se* remove.
- **Validação da correção:** 200 tentativas com a rota disparada logo após enfileirar (viés a favor do worker vencer a corrida) e 50 tentativas com um atraso de 10ms dando ao worker uma chance real — em todas as 250, nunca um job "processando" teve o PDF apagado nem sumiu do store incorretamente.
- **Testes de regressão:** `TestRemoverSeNaoProcessando` (3 testes unitários no `JobStore`) + `TestRemoverSeNaoProcessandoSobContencao.test_delete_concorrente_com_worker_nunca_apaga_pdf_de_job_processando` (30 iterações sob contenção real) — 4 no total.
- **Risco residual:** a segurança depende de todo código futuro usar `remover_se_nao_processando()` em vez de um check-then-act manual — não há trava estrutural impedindo alguém de reintroduzir o padrão antigo numa rota nova.

### BUG-13 — `download-zip` em memória, quebra se um arquivo sumir

- **Estado:** VÁLIDO (reproduzido) · P2
- **Validação:** job `"concluido"` cujo `caminho_saida` aponta pra um arquivo nunca criado → `api.baixar_zip()` levanta `FileNotFoundError` não tratado (viraria `500`).
- **Correção aplicada:** `backend/src/routes/api.py` — `io.BytesIO()` trocado por `tempfile.SpooledTemporaryFile(max_size=10 MiB)` (limita RAM, transborda pra disco acima do teto — a resposta ainda não é streaming incremental de verdade, o zip inteiro é montado antes de começar a responder). Cada `zf.write()` embrulhado em `try/except OSError`, pulando o arquivo ausente; se todos os arquivos do lote sumirem, devolve `404` limpo em vez de um zip com zero entradas.
- **Validação da correção:** mesmo cenário — devolve `404` (`nenhum arquivo disponivel para baixar (removidos durante a preparacao do zip)`), sem exceção não tratada.
- **Testes de regressão:** `TestDownloadEndpoints.test_zip_pula_arquivo_que_sumiu_em_vez_de_derrubar_o_download`, `test_zip_com_todos_os_arquivos_sumidos_devolve_404` (`backend/tests/test_app.py`).
- **Risco residual:** a resposta ainda materializa o zip inteiro antes de responder (streaming incremental de verdade exigiria um escritor de zip em chunks, fora do escopo pedido aqui).

### BUG-14 — Foco de teclado perdido a cada poll

- **Estado:** VÁLIDO (confirmado por inspeção) · P2 — mesma limitação de tooling do BUG-09 (sem Node/browser, sem suíte JS).
- **Validação:** `jobsList.innerHTML = ...` em `renderizarFila()` recria a subárvore inteira do DOM a cada poll — qualquer elemento focado dentro dela (ex.: um botão `.acao-remover`) é destruído; o navegador não consegue manter foco num nó destruído, então ele volta pro `<body>` no próximo ciclo (até 1,5s depois), indefinidamente. `#queue-summary` não tinha `aria-live`.
- **Correção aplicada:** `renderizarFila()` agora captura o `data-job-id` do `document.activeElement` (se estiver dentro de `jobsList`) antes de substituir o HTML, e restaura o foco no botão equivalente pós-render via `querySelector`. Adicionado `aria-live="polite"` em `#queue-summary`.
- **Validação da correção:** rastreamento manual do ciclo captura→substitui→restaura contra a correção.
- **Testes de regressão:** nenhum (ver justificativa).
- **Risco residual:** só cobre o botão de remover (o único citado no relatório); o link de download não carrega `data-job-id`, então perder foco nele não é restaurado — fora do escopo literal do item.

### BUG-15 — Versão declarada em quatro lugares

- **Estado:** VÁLIDO (reproduzido) · P2
- **Validação:** `pyproject.toml` (`2.1.0`), `pdf_to_md.__version__` (`2.1.0`), comentário do extra `web` (`"(v3.0)"`) e `frontend/index.html` (`"v3.0"` fixo) — quatro declarações independentes, duas delas discordando das outras duas.
- **Correção aplicada:** `pyproject.toml` — `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "pdf_to_md.__version__"}`. `frontend/index.html`/`app.js` — subtítulo populado em runtime via novo `carregarVersao()` (mesmo padrão de `carregarMotor()`), lendo de `/api/health` (que já reportava `pdf_to_md.__version__`). Comentário `"(v3.0)"` removido.
- **Validação da correção:** venv limpo, `pip install -e .`, `importlib.metadata.version("pdf-to-md")` bate com `pdf_to_md.__version__` (`2.1.0`); servidor real rodando confirma que `index.html` não referencia mais `"v3.0"` e `/api/health` responde `2.1.0`.
- **Testes de regressão:** `TestEmpacotamento.test_versao_e_dinamica_a_partir_de_pdf_to_md_dunder_version`, `test_frontend_nao_tem_versao_fixa_no_html` (`test_pdf_to_md.py`).
- **Risco residual:** nenhum.

### BUG-16 — Extra `dev` não permite rodar os testes do backend

- **Estado:** VÁLIDO (reproduzido) · P2
- **Validação:** venv limpo, `pip install -e ".[dev]"`, `pytest backend/` → `ModuleNotFoundError: No module named 'fastapi'`.
- **Achado adicional durante a verificação:** mesmo depois de trazer `fastapi`/`uvicorn`, `backend/tests/test_app.py` ainda falhava ao importar com `RuntimeError: The starlette.testclient module requires the httpx2 package to be installed` — exigência do próprio `starlette` (não deste projeto). Só não aparecia no `.venv` de desenvolvimento existente porque o `httpx` clássico chegava ali transitivamente via `docling` (com um aviso de depreciação, não erro).
- **Correção aplicada:** `pyproject.toml` — `dev = ["fpdf2>=2.7,<3", "pdf-to-md[web]", "httpx2>=2,<3"]`. `dependencies.md`/`README.md` atualizados (tabela de extras, combinações típicas).
- **Validação da correção:** venv limpo, `pip install -e ".[dev]"` → `test_pdf_to_md.py` (81 testes, `OK`) e `backend/tests/` (68 testes, `passed`) rodam com sucesso, sem `docling` instalado.
- **Testes de regressão:** `TestEmpacotamento.test_extra_dev_traz_o_necessario_para_rodar_backend_tests` (`test_pdf_to_md.py`).
- **Risco residual:** nenhum.

### BUG-17 — UI afirma GPU/Docling que podem não existir

- **Estado:** VÁLIDO (confirmado por inspeção) · P3
- **Validação:** grep direto — `"Aguardando GPU"` (badge + resumo) e `"...no motor Docling"` (rodapé) hardcoded em `frontend/app.js`/`index.html`, incondicionalmente, independente do motor real (`docling`/`simples`).
- **Correção aplicada:** textos trocados por versões neutras ao motor: `"Na fila"` (badge), `"N na fila"` (resumo), `"fila processa 1 PDF por vez"` (rodapé, sem menção a Docling).
- **Validação da correção:** grep confirma zero ocorrências de `"GPU"`/`"Docling"` em `frontend/app.js`/`index.html`.
- **Testes de regressão:** `TestEmpacotamento.test_frontend_nao_afirma_gpu_ou_docling_incondicionalmente`.
- **Risco residual:** nenhum.

### BUG-18 — `disabled` num `<a>` não é HTML válido

- **Estado:** VÁLIDO (confirmado por inspeção) · P3
- **Validação:** `<a id="download-all" ... disabled>` — `disabled` é atributo de controle de formulário, ignorado pelo navegador em `<a>`; só "funcionava" via CSS `pointer-events:none` + JS interceptando o clique.
- **Correção aplicada:** `#download-all` alterna `aria-disabled="true"/"false"` e o próprio atributo `href` (presente só quando habilitado); `role="button"` adicionado. Seletor CSS trocado de `[disabled]` para `[aria-disabled="true"]`.
- **Validação da correção:** grep confirma ausência de `disabled>` na tag e presença de `aria-disabled`; seletor CSS correspondente presente em `app.css`.
- **Testes de regressão:** `TestEmpacotamento.test_download_all_usa_aria_disabled_em_vez_de_disabled_no_a`.
- **Risco residual:** nenhum.

### BUG-19 — Google Fonts bloqueia a renderização sem internet

- **Estado:** VÁLIDO (reproduzido) · P3
- **Validação:** `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` cross-origin, render-blocking por padrão — confirmado que o `<link>` estava presente e era a única dependência externa do frontend (`frontend.md`/`dependencies.md` já documentavam isso).
- **Correção aplicada:** baixados os `.woff2` reais (subset `latin` — cobre pt-BR integralmente, acentos como `ã`/`ç`/`õ` já dentro de U+0000-00FF) pra `frontend/fontes/`; `@font-face` locais em `app.css`; `<link>` removido. Achado ao baixar: Sora e Work Sans são servidas como fonte variável única cobrindo todos os pesos pedidos (mesma URL por peso na resposta do provedor original) — só 4 arquivos únicos necessários, não um por peso. `frontend/fontes/OFL.txt` incluído (SIL OFL 1.1, licença das três famílias).
- **Validação da correção:** servidor real rodando — `index.html` não referencia mais `fonts.googleapis.com`/`fonts.gstatic.com`; os 4 `.woff2` servem com `200` através do mesmo `StaticFiles` mount.
- **Testes de regressão:** `TestEmpacotamento.test_frontend_nao_tem_link_externo_para_google_fonts` (verifica ausência dos dois domínios + presença dos 4 arquivos).
- **Documentação:** `frontend.md`/`dependencies.md` atualizados (não afirmam mais Google Fonts como "único recurso externo").
- **Risco residual:** só o subset `latin` foi baixado — texto num script não coberto por U+0000-00FF (cirílico, vietnamita, etc.) cairia pra fonte de sistema. Aceitável dado o escopo pt-BR/en atual da UI.

### BUG-20 — Erros de rede invisíveis na UI

- **Estado:** VÁLIDO (confirmado por inspeção) · P3 — mesma limitação de tooling do BUG-09/14.
- **Validação:** `catch` de `atualizarFila()` vazio; `carregarMotor()` só roda uma vez no load — combinado com o BUG-01, o cenário completo era: worker morre, jobs ficam presos, nenhuma camada emite sinal.
- **Correção aplicada:** contagem de falhas consecutivas de poll (`marcarFalhaDePoll()`, cobrindo tanto `!resp.ok` quanto exceções de rede, protegida pelo guard de sequência do BUG-09 pra uma resposta obsoleta não contar como falha espúria); após 3 seguidas, mostra um banner `"Sem conexão com o servidor"` (`#conexao-alerta`, `aria-live="polite"`). O próximo poll bem-sucedido (`marcarSucessoDePoll()`) esconde o banner e, se ele estava visível, rechama `carregarMotor()`.
- **Validação da correção:** verificação estrutural (chaves/parênteses balanceados, padrão consistente com o resto do arquivo) + servidor real confirmando que `index.html`/`app.js`/`app.css` continuam servindo corretamente após a mudança.
- **Testes de regressão:** `TestEmpacotamento.test_frontend_avisa_sobre_perda_de_conexao`.
- **Risco residual:** limiar fixo (3 falhas), sem backoff exponencial — a UI continua fazendo poll na cadência normal mesmo com o servidor fora.

### BUG-21 — `/api/motor` devolve 500 quando o pool não foi inicializado

> **Errata (ver `bug_report-2.md`, BUG-26):** o estado abaixo foi
> reclassificado de `VÁLIDO (reproduzido)` para `VÁLIDO (não alcançável
> hoje)`. `motor_pool.inicializar()` roda no `lifespan`, e ASGI garante
> que o lifespan termina antes de qualquer request ser despachada — sob a
> aplicação real rodando normalmente, `_motor` nunca é `None` quando uma
> requisição HTTP chega em `/api/motor`. A validação exigiu resetar o
> estado do módulo manualmente, um caminho que nenhum request real
> percorre — mesmo padrão do BUG-11.

- **Estado:** VÁLIDO (não alcançável hoje) · P3
- **Validação:** `motor_pool._motor = None` + chamada direta a `api.motor()` → `RuntimeError` não tratada.
- **Correção aplicada:** `backend/src/routes/api.py` — `motor()` captura `RuntimeError` e relança como `HTTPException(503, ...)`.
- **Validação da correção:** mesmo cenário — `HTTPException(503, "motor_pool nao inicializado...")`.
- **Testes de regressão:** `TestApp.test_motor_devolve_503_se_pool_nao_inicializado` (`backend/tests/test_app.py`, reseta `motor_pool` dentro do `with TestClient(...)` pra não ser sobrescrito pelo `lifespan`).
- **Risco residual:** nenhum — condição não alcançável pelo lifespan normal hoje, mas agora diagnosticável se algum dia for.

### BUG-22 — `parar_worker` nunca faz shutdown gracioso com fila cheia

- **Estado:** VÁLIDO (reproduzido) · P3
- **Validação:** backlog de 6 jobs (~3s pra drenar via motor stub com atraso de 0,5s) + `jobs.parar_worker(timeout=1.0)` → retorna após exatamente 1,0s (join estourou), com a thread ainda viva rodando em background — confirmado por um `OSError` real de limpeza de diretório temporário colidindo com a thread órfã.
- **Correção aplicada:** `backend/src/services/jobs.py` — `_loop()` continua bloqueando indefinidamente em `_fila.get()` (sem tributo de polling quando ocioso), mas checa `_parar_evento.is_set()` logo após cada dequeue, antes de processar; `parar_worker()` marca o evento e injeta um `_SENTINEL` (acorda um `get()` bloqueado mesmo com fila vazia). `_worker_thread` só é zerado pra `None` se a thread realmente parou dentro do timeout. Novo `_drenar_fila_abandonada()` esvazia qualquer item deixado pra trás, pra um `iniciar_worker()` seguinte não pegar `job_id`s de uma sessão anterior.
- **Divergência descoberta durante a verificação:** uma primeira versão desta correção usou `_fila.get(timeout=0.5)` (polling) em vez do design acima — funcionalmente correta, mas adicionava até 0,5s de latência em **todo** shutdown, inclusive o caso ocioso (nada na fila). Isso passou despercebido até `pytest --durations` revelar a suíte `backend/` subindo de ~2s pra ~20s (dezenas de testes disparando `TestClient`, cada um pagando o pedágio no `lifespan` shutdown). Corrigido trocando polling por um sentinela de despertar + checagem pós-dequeue, eliminando o custo no caso ocioso.
- **Validação da correção:** mesmo cenário de backlog — retorna em ~0,5s (limitado pelo job já em andamento terminar, não pelo backlog inteiro); só 1 de 6 jobs conclui (o que já estava em andamento), os outros 5 ficam `na_fila`; reiniciar o worker depois não duplica a thread. Suíte `backend/` de volta a ~3,2s em 3 execuções consecutivas.
- **Testes de regressão:** `TestPararWorkerComBacklog.test_para_rapido_mesmo_com_backlog_maior_que_o_timeout`, `test_reiniciar_apos_parar_nao_duplica_a_thread_worker` (`backend/tests/test_jobs.py`).
- **Risco residual:** se o job *já em andamento* no momento do shutdown demorar mais que `timeout`, o `join()` ainda estoura e a thread fica órfã (mesmo fallback daemon de antes) — inerente a "deixar o trabalho em andamento terminar" sem abortar uma conversão no meio (o que arriscaria só o processo, não o arquivo de saída, já protegido por `escrever_atomico`).

### BUG-23 — Docstring desatualizado

- **Estado:** VÁLIDO (confirmado por inspeção) · P3
- **Validação:** `backend/src/services/motor_pool.py` citava `"lifespan de webapp.main"` — esse módulo não existe neste código-base; o app e seu lifespan estão em `backend.src.app`.
- **Correção aplicada:** docstring atualizado para citar `backend.src.app`.
- **Validação da correção:** grep confirma ausência de `"webapp.main"` em todo o repositório.
- **Testes de regressão:** `TestEmpacotamento.test_motor_pool_docstring_nao_cita_modulo_inexistente`.
- **Risco residual:** nenhum.

---

## 4. Estado das suítes

Capturado via `git worktree` no commit inicial (`a7f3fc6`) e no HEAD final (`a42ecc5`), mesmo `.venv`.

### Antes (`a7f3fc6`)

```
$ python -m unittest test_pdf_to_md
Ran 76 tests in 2.148s
FAILED (failures=2)

$ pytest backend/
52 passed in 1.09s
```

### Depois (`a42ecc5`, HEAD)

```
$ python -m unittest test_pdf_to_md
Ran 87 tests in 2.020s
FAILED (failures=2)

$ pytest backend/
71 passed, 11 subtests passed in 3.18s
```

**Falhas pré-existentes (antes e depois, idênticas — não são regressão):**

- `TestSelecaoMotor.test_auto_cai_para_simples`
- `TestSelecaoMotor.test_docling_explicito_falha_com_mensagem_util`

Ambas documentadas no `README.md`: assumem um ambiente *sem* Docling instalado pra exercitar o caminho de fallback/erro de `selecionar_motor`; como este ambiente de validação tem `docling` 2.123.1 instalado, elas falham — limitação de isolamento da suíte, não regressão introduzida por este trabalho (confirmado idêntico nos dois commits).

**Delta:** +11 testes em `test_pdf_to_md.py` (76→87), +19 em `backend/tests/` (52→71, +11 subtests) — total +30, batendo com a soma da coluna "Testes adicionados" do quadro-resumo.

---

## 5. Pendências

### Itens válidos não corrigidos integralmente

- **BUG-10** — o padrão `HOST=0.0.0.0` **não** foi revertido para `127.0.0.1`. Motivo: decisão de produto conflitante — commit `8cd4860`, feito mais cedo nesta mesma sessão, mudou deliberadamente o padrão de `127.0.0.1` pra `0.0.0.0` especificamente pra resolver inacessibilidade via rede local. Reverter aqui desfaria essa correção anterior silenciosamente. Perguntado ao usuário; resposta: manter `0.0.0.0`, aplicar só o aviso visível de exposição (feito). O restante da proposta (reverter o padrão) fica pendente de uma decisão de produto explícita, não de trabalho técnico.

### Alterações de documentação feitas

Seção "Documentação a corrigir" de `bugs.md`, todas endereçadas:

- `architecture.md` — reescrita a afirmação sobre `threading.Lock` cobrir "toda leitura/escrita"; agora especifica que o lock cobre o dicionário do `JobStore`, não os campos de um `Job` individual, e explica por que isso é seguro (atomicidade de atributo único em CPython) em vez de prometer uma garantia mais forte que o código não dá.
- `dependencies.md` — `start.sh`/`restart.sh` na tabela de variáveis de ambiente trocados por `scripts/start.sh`/`scripts/restart.sh`.
- `backend.md` — snippet de `_atualizar_estimativa` recebeu de volta a guarda `if not job.paginas_totais: return` que estava elidida, mais uma nota sobre por que resultados `"pulado"` (BUG-11) são excluídos da chamada.
- Variáveis de ambiente do BUG-03 (`MAX_UPLOAD_BYTES`/`MAX_UPLOAD_FILES`/`MAX_UPLOAD_PAGES`) — documentadas junto com a própria correção do BUG-03 (commit `a66c484`), não como item avulso.
- Default de `HOST` (BUG-10) — não há novo default a documentar, já que a mudança não foi aplicada (ver acima); documentado em vez disso o novo aviso de `start.sh` em `architecture.md`.

Nada ficou pendente nesta seção.

### Problemas encontrados durante a execução que não constam em `bugs.md`

`bugs.md` declara explicitamente que `backend/tests/`, `test_pdf_to_md.py`, `scripts/*.sh` e `README.md` não foram revisados na produção do relatório original. Como consequência natural de implementar as correções pedidas (que vivem justamente nesses arquivos: testes de regressão, o aviso do BUG-10 em `scripts/start.sh`, atualizações de instalação no `README.md`), esses arquivos foram tocados — mas não houve uma auditoria independente à procura de bugs *novos* neles, fora do que already ficou registrado abaixo:

1. **`httpx2` ausente do extra `dev`** (achado verificando o BUG-16): mesmo com `fastapi`/`uvicorn` presentes, `backend/tests/test_app.py` ainda falhava ao importar — `starlette.testclient` exige `httpx2` explicitamente em versões recentes do `starlette`. Só não aparecia no `.venv` de desenvolvimento pré-existente porque `httpx` clássico chegava ali transitivamente via `docling` (com aviso de depreciação, não erro). Corrigido junto do BUG-16 (commit `4ec2685`).
2. **`_worker_thread` zerado para `None` mesmo com a thread ainda viva** (achado verificando o BUG-22): o código original de `parar_worker()` fazia `_worker_thread = None` incondicionalmente após o `join(timeout=...)`, mesmo quando o join estourava e a thread continuava rodando — uma chamada seguinte a `iniciar_worker()` teria subido uma **segunda** thread consumindo a mesma fila. Corrigido junto do BUG-22 (commit `c90a767`).
3. **Regressão de performance auto-introduzida e corrigida na mesma sessão**: a primeira tentativa de correção do BUG-22 (polling com `_fila.get(timeout=0.5)`) fazia a suíte `backend/` ir de ~2s pra ~20s por causa do pedágio de 0,5s em todo shutdown de worker via `TestClient`. Detectado com `pytest --durations` antes do commit final; a versão commitada (`c90a767`) não tem esse custo — ver detalhe no item BUG-22 acima.

Nenhum outro problema fora do escopo de `bugs.md` foi procurado ativamente (não houve auditoria independente de `scripts/*.sh`, `test_pdf_to_md.py` ou `README.md` além do necessário pra implementar os 23 itens e a seção de documentação).
