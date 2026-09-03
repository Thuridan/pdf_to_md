# bug_report-2.md — segunda rodada (execução de `bugs-2.md`)

## 1. Cabeçalho

- **Data de execução:** 2026-09-02
- **Commit inicial:** `72224de` (Add bug_report.md for the bugs.md review — estado do repo ao ler `bugs-2.md`)
- **Commit final:** `3634932` (Log an actionable message when the app starts with no engine — BUG-27)
- **Sistema operacional:** Ubuntu 24.04.4 LTS (x86_64) — mesmo ambiente da primeira rodada
- **Ambiente de validação:**
  - Python 3.12.3
  - `docling` 2.123.1
  - `pypdfium2` 5.13.0
  - `fastapi` 0.141.1
  - `starlette` 1.6.0
  - `setuptools` 84.0.0
  - `uvicorn` 0.52.4
  - `httpx2` 2.12.0
  - Idêntico ao ambiente da primeira rodada — nenhuma dependência nova instalada nesta rodada.

**Nota de escopo:** esta rodada revisou `bug_report.md` e os trechos de código que ele cita, não o diff bruto dos 23 commits da primeira rodada (conforme delimitado em `bugs-2.md`). Onde a verificação exigiu, o código real foi lido diretamente (não só o relatório) — nenhuma divergência entre `bug_report.md` e o código real foi encontrada.

**Nota de plataforma — BUG-24:** a verificação exigida por `bugs-2.md` ("a suíte tem de terminar em `OK` em dois ambientes: um com `docling` instalado e outro sem") foi cumprida rodando `python -m unittest test_pdf_to_md` neste `.venv` de desenvolvimento (`docling` 2.123.1 instalado) **e** num venv limpo construído só com `pip install -e ".[dev]"` (sem `docling`) — `OK` nos dois.

---

## 2. Quadro-resumo

| ID | Prioridade | Estado | Corrigido | Commit | Testes adicionados |
|---|---|---|---|---|---|
| BUG-24 | P1 | VÁLIDO (reproduzido) | sim | `096fb2e` | 0 (3 testes existentes corrigidos, não substituídos) |
| BUG-25 | P2 | VÁLIDO (confirmado por inspeção — o próprio `bug_report.md` já admitia) | sim | `8500ca6` | +1 líquido (2 novos, 1 removido) |
| BUG-26 | P2 | VÁLIDO (confirmado por inspeção — erro de registro, não de código) | sim (só o registro) | `156404c` | 0 (nenhum código alterado, por instrução explícita) |
| BUG-27 | P3 | VÁLIDO (reproduzido) | sim | `3634932` | 1 |

**Contagens:**
- Válidos (reproduzidos): 2 (BUG-24, BUG-27)
- Válidos (confirmados por inspeção): 2 (BUG-25, BUG-26)
- Inválidos: 0
- Corrigidos: 4 de 4
- Total de testes adicionados nesta rodada: **2** líquidos (0 do BUG-24 + 1 do BUG-25 + 0 do BUG-26 + 1 do BUG-27) — bate com o delta medido (`test_pdf_to_md.py` 87→87; `backend/tests/` 71→73)

**Critério de conclusão da rodada** (`bugs-2.md`): "`test_pdf_to_md.py` terminar em `OK`, sem falhas" — **atendido**. Ver seção 4.

---

## 3. Detalhe por item

### BUG-24 — Dois testes dependem de o Docling não estar instalado, e mantêm a suíte vermelha

- **Estado:** VÁLIDO (reproduzido) · P1
- **Validação:** neste ambiente (`docling` 2.123.1 instalado), `python -m unittest test_pdf_to_md.TestSelecaoMotor` falhava em `test_auto_cai_para_simples` (`'docling' != 'simples'`) e `test_docling_explicito_falha_com_mensagem_util` (`ErroConversao not raised`). Causa confirmada por inspeção: `MotorDocling.disponivel()` usa `importlib.util.find_spec("docling")`, que consulta `sys.modules` primeiro mas cai para o disco se não encontrar nada lá — `remover_stub_docling()` só limpa `sys.modules`, então com o pacote real instalado no disco o teste media o ambiente, não a lógica de `selecionar_motor()`.
- **Correção aplicada:** `test_pdf_to_md.py`, `TestSelecaoMotor` — os 4 testes da classe passaram a usar `unittest.mock.patch.object(m.MotorDocling, "disponivel", return_value=...)` em vez de instalar/remover módulos falsos de `sys.modules`. `test_auto_prefere_docling` também foi convertido (verificado por inspeção que já passava "pelo motivo certo" mesmo antes — `find_spec` consulta `sys.modules` primeiro, então o stub injetado por `instalar_stub_docling()` já vencia a busca em disco —, mas a conversão remove a dependência dessa característica de implementação do `find_spec` e deixa as 4 verificações consistentes).
- **Validação da correção:** `python -m unittest test_pdf_to_md.TestSelecaoMotor` → `OK` (4 testes) neste `.venv` (docling instalado) **e** num venv limpo construído via `pip install -e ".[dev]"` (sem docling) — `OK` nos dois. Suíte completa: `python -m unittest test_pdf_to_md` → `Ran 87 tests ... OK` nos dois ambientes (era `FAILED (failures=2)` nos dois antes).
- **Testes de regressão:** nenhum teste novo — os 4 testes existentes de `TestSelecaoMotor` foram corrigidos para testar a coisa certa, não substituídos por novos.
- **Documentação:** removida do `README.md` a nota que documentava as duas falhas como esperadas; corrigidas ali e em `architecture.md` as contagens de teste desatualizadas (76→87, 52→71) encontradas na mesma passada.
- **Risco residual:** nenhum — a suíte agora termina `OK` independente do que estiver instalado no ambiente.

### BUG-25 — O teste de regressão do BUG-06 passa com ou sem o bug

- **Estado:** VÁLIDO (confirmado por inspeção) · P2 — o próprio `bug_report.md` já admitia a lacuna ("não garante disparar a janela em toda execução"); não havia o que "reproduzir" além de reler essa admissão e confirmar por inspeção do teste antigo que ele de fato não distinguia as duas versões do código.
- **Validação:** leitura do teste anterior (`TestOrdemDeAtribuicaoDoJob`, baseado em polling com atraso artificial do motor stub) — confirmado que ele apenas registra violações *se* observar a janela, sem garantir observá-la; um teste que não força a condição não pode falhar de forma confiável na presença do bug.
- **Correção aplicada — opção (a) escolhida:** substituído por verificação estrutural via AST. Novo `TestOrdemDeAtribuicaoDoJob` em `backend/tests/test_jobs.py` faz `ast.parse(inspect.getsource(jobs._processar))` e afirma, na ordem textual dos `ast.Assign`, que `job.iniciado_em` precede `job.status = "processando"`, e que `job.caminho_saida` precede `job.status = "concluido"` dentro do mesmo bloco `if`.
- **Validação da correção (exigida explicitamente por `bugs-2.md`):** reversão temporária das duas atribuições em `backend/src/services/jobs.py` (script local, nunca commitado) → os dois novos testes **falharam** com mensagem clara (`3 not less than 2`, etc.); arquivo restaurado (`git diff` limpo) → os dois testes voltaram a **passar**. Evidência de que o teste de fato falha na presença do bug, não só na ausência.
- **Testes de regressão:** `TestOrdemDeAtribuicaoDoJob.test_status_processando_vem_depois_de_iniciado_em`, `test_status_concluido_vem_depois_de_caminho_saida` (2 novos, substituindo 1 antigo — líquido +1).
- **Risco residual:** o teste é deliberadamente acoplado à forma textual de `_processar()` (comentado explicitamente no docstring da classe) — uma refatoração legítima da função que preserve o invariante mas mude a estrutura do código (ex.: extrair as atribuições para um método `Job.marcar_processando()`) quebraria o teste mesmo sem reintroduzir o bug, exigindo atualização manual. Aceito como o trade-off da opção (a): um teste "feio" que falha de verdade vale mais que um teste "limpo" que não falha nunca.

### BUG-26 — BUG-11 (e também BUG-21) classificados como reproduzidos sem serem alcançáveis em produção

- **Estado:** VÁLIDO (confirmado por inspeção) · P2 — erro de *registro*, não de código; não havia comportamento para reproduzir, só uma etiqueta para corrigir.
- **Validação:** releitura do texto do próprio BUG-11 em `bug_report.md`, que já admitia "não há endpoint de retry/reenfileiramento na API pública hoje — o caminho só é alcançável chamando `_processar()` diretamente". Confirmado que essa chamada dupla a uma função privada não corresponde a nenhum fluxo real da aplicação.
- **Revisão adicional pedida por `bugs-2.md`** ("revise se algum outro item merece a mesma reclassificação"): reli os 15 itens marcados `VÁLIDO (reproduzido)` contra o critério "a validação exercitou o caminho que a aplicação realmente percorre, ou um caminho sintético?". **BUG-21 se encaixa no mesmo padrão**: a validação exigiu resetar `motor_pool._motor`/`_cfg` para `None` manualmente. Confirmado por leitura de `backend/src/app.py` que `motor_pool.inicializar()` roda dentro do `lifespan` do FastAPI, e ASGI (Starlette/uvicorn) garante que o `lifespan` termina antes de qualquer requisição ser despachada — sob a aplicação real rodando normalmente, `_motor` nunca é `None` quando uma requisição HTTP chega em `/api/motor`. Mesmo padrão do BUG-11: validado invocando a função real, mas com um estado interno que nenhum request real produz.
  - Os outros 13 itens `VÁLIDO (reproduzido)` foram revisados um a um e **não** se encaixam no padrão: seus gatilhos são todos produzíveis por uso realista (entrada malformada via CLI real, `pip install` real, requisições HTTP concorrentes reais, servidor real rodando com `curl`) — nenhum exige manipular estado interno que nenhum caminho da aplicação poderia produzir. Justificativa item a item na seção "Pendências"/histórico do commit `156404c`.
  - Vale registrar a observação do próprio `bugs-2.md`: 23 de 23 válidos e zero inválidos na primeira rodada era, de fato, um resultado que merecia mais ceticismo do que recebeu — esta reclassificação é a correção direta disso, e a revisão dos 13 itens restantes é o exercício desse ceticismo aplicado ao resto do relatório, não só ao item apontado.
- **Correção aplicada:** **nenhum código alterado**, por instrução explícita de `bugs-2.md`. Só o registro em `bug_report.md`: quadro-resumo e as duas seções de detalhe (BUG-11 e BUG-21) marcados com uma nota de errata e reclassificados para `VÁLIDO (não alcançável hoje)`; contagens da seção 2 ajustadas (13 reproduzidos, 8 confirmados por inspeção, 2 não alcançáveis hoje). O texto de validação original de ambos os itens foi preservado intacto — já estava correto, só a etiqueta não estava.
- **Testes de regressão:** nenhum (mudança de documentação/classificação, não de comportamento).
- **Risco residual:** nenhum — as correções de código do BUG-11 e do BUG-21 continuam válidas e permanecem aplicadas; só a forma como o relatório as descreve mudou.

### BUG-27 — Risco residual do BUG-02 declarado como "nenhum"

- **Estado:** VÁLIDO (reproduzido) · P3
- **Validação:** venv limpo, `pip install -e . --no-deps` + `fastapi`/`uvicorn`/`python-multipart` (sem `pypdfium2` nem `docling`) → `uvicorn backend.src.app:app` falha com um traceback cru de `pdf_to_md.ErroConversao: Nenhum motor disponivel (...)`, sem nenhuma orientação sobre como corrigir a instalação.
- **Correção aplicada:** `backend/src/app.py` — `lifespan()` captura `pdf_to_md.ErroConversao` em volta de `motor_pool.inicializar()`, loga uma mensagem acionável (`"Nenhum motor de conversao disponivel; instale com \`pip install '.[docling]'\` ou \`pip install '.[simples]'\`."`) e relança — o *fail fast* é preservado (o processo continua saindo com erro), só a mensagem melhora.
- **Validação da correção:** mesmo venv limpo — a mensagem acionável agora aparece antes do traceback, `Application startup failed. Exiting.` continua acontecendo (não sobe degradado). Instalação normal (motor presente) testada em paralelo via `scripts/start.sh` — sobe e responde `/api/health` normalmente, sem regressão.
- **Testes de regressão:** `TestApp.test_startup_sem_motor_loga_mensagem_acionavel_e_continua_falhando` (`backend/tests/test_app.py`) — usa `patch.object(motor_pool, "inicializar", side_effect=m.ErroConversao(...))` e confirma via `assertLogs` que a mensagem contém `"pip install"`, e via `assertRaises` que o `ErroConversao` ainda propaga (startup continua falhando).
- **Documentação:** seção do BUG-02 em `bug_report.md` atualizada — "Risco residual" trocado de "nenhum" para a descrição real do cenário remanescente, com nota de errata apontando para este item.
- **Risco residual:** nenhum novo — o cenário (instalação sem nenhum motor) continua existindo (é fail-fast por design, não uma falha a eliminar), só passou a ser diagnosticável pelo operador.

---

## 4. Estado das suítes

Capturado via `git worktree` no commit inicial desta rodada (`72224de`) e no HEAD final (`3634932`), mesmo `.venv`.

### Antes (`72224de` — fim da primeira rodada)

```
$ python -m unittest test_pdf_to_md
Ran 87 tests in 2.214s
FAILED (failures=2)

$ pytest backend/
71 passed, 11 subtests passed in 3.32s
```

### Depois (`3634932`, HEAD)

```
$ python -m unittest test_pdf_to_md
Ran 87 tests in 2.165s
OK

$ pytest backend/
73 passed, 11 subtests passed in 3.07s
```

**Critério de conclusão da rodada atendido:** `test_pdf_to_md.py` termina em `OK`, sem falhas — confirmado neste `.venv` (docling instalado) e, para o BUG-24 especificamente, também num venv limpo sem docling (ver seção 3).

**Delta:** `test_pdf_to_md.py` estável em 87 testes (0 novos, mas 3 corrigidos); `backend/tests/` +2 (71→73), batendo com a soma da coluna "Testes adicionados" do quadro-resumo (+1 BUG-25, +1 BUG-27).

---

## 5. Pendências

Nenhum item desta rodada ficou com código pendente — os 4 (BUG-24 a BUG-27) foram corrigidos integralmente, e o critério de conclusão da rodada (suíte `test_pdf_to_md.py` em `OK`) foi atendido.

### Decisão registrada, não é bug desta rodada

Conforme a seção "Decisão pendente (não é bug)" de `bugs-2.md`: o `HOST=0.0.0.0` sem autenticação (BUG-10 da primeira rodada) continua sendo uma escolha de conveniência aceitável **hoje**, mas fica vinculada ao seguinte pré-requisito para quando a aplicação passar a atender múltiplos usuários:

- `GET /api/jobs` devolve a fila inteira a qualquer cliente que alcance o processo.
- `GET /api/download-zip` sem `?ids=` empacota **todos** os jobs concluídos do processo, não só os do cliente que pediu.
- Ou seja: hoje, a segunda pessoa a abrir a página vê e pode baixar os documentos da primeira. Isso é aceitável para a ferramenta local/single-tenant que o projeto documenta ser (`architecture.md`), mas deixa de ser quando (se) houver isolamento por usuário no roadmap.

Registrado aqui por instrução explícita de `bugs-2.md`: isolamento por usuário e a decisão de bind (`HOST`) **devem ser tratados como um item só** na próxima rodada, não separadamente — resolver um sem o outro não fecha o problema (abrir `0.0.0.0` sem isolamento por usuário expõe a fila de todo mundo à rede local; adicionar isolamento por usuário sem revisar o bind não muda o raio de exposição da rede). Nenhuma ação tomada nesta rodada além deste registro.

### Problemas encontrados durante a execução que não constam em `bugs-2.md`

> **Errata (rodada 3, TAREFA-7):** esta seção dizia "Nenhum", o que
> contradizia o próprio detalhe do BUG-24 (acima) — corrigido aqui, só o
> registro, nenhum código alterado nesta correção.

Um: verificando o BUG-24, as contagens de teste em `README.md` e
`architecture.md` (`76 testes`/`52 testes`) estavam desatualizadas em
relação à suíte real (na época, já `87`/`71` depois das correções da
própria rodada 2) — achado fora dos 4 itens de `bugs-2.md`, corrigido na
mesma passada do BUG-24 (commit `096fb2e`, ver seu detalhe acima) em vez de
registrado como item avulso. Fora isso, a revisão desta rodada ficou dentro
do escopo declarado (`bug_report.md` e os trechos de código que ele cita) e
não revelou outras divergências entre o relatório e o código real.
