# bug_report-3.md — terceira rodada (execução de `prompt-rodada-3.md`)

## 1. Cabeçalho

- **Data de execução:** 2026-09-03
- **Commit inicial:** `4cce636` (Add bug_report-2.md for the bugs-2.md review — estado do repo ao ler `prompt-rodada-3.md`)
- **Commit final:** `6f84fc1` (Fix the self-contradicting "fora do escopo" section in bug_report-2.md — TAREFA-7)
- **Sistema operacional:** Ubuntu 24.04.4 LTS (x86_64), 62 GiB de RAM — mesma máquina das rodadas anteriores
- **Ambiente de validação:**
  - Python 3.12.3
  - `docling` 2.123.1
  - `pypdfium2` 5.13.0
  - `fastapi` 0.141.1 · `starlette` 1.6.0 · `uvicorn` 0.52.4 · `httpx2` 2.12.0
  - `setuptools` 84.0.0
  - Idêntico às rodadas anteriores — nenhuma dependência nova instalada.

**Documentos reais usados para calibração/validação (TAREFA-3, TAREFA-4):**
- `teste.pdf` (~42 MB, 1310 páginas, manual de administração de firewall real) — apareceu na raiz do repositório fora de qualquer chamada de ferramenta deste agente, entre o fim da rodada 2 e o início desta. Não foi criado por nenhum script deste relatório; tratado como um documento de teste real fornecido para esta rodada (o próprio `prompt-rodada-3.md` prevê "manuais e documentação de fornecedor" como o caso de uso central). **Não commitado** (arquivo grande, não rastreado, permanece só no filesystem local).
- `LGPD.pdf` (13 MB, 26 páginas, já presente no repositório — gitignored, usado em rodadas anteriores) — na verificação desta rodada, constatou-se que é um documento genuinamente **digitalizado** (zero texto extraível em todas as 26 páginas, verificado exaustivamente, não por amostragem).
- Um terceiro documento "misto" foi **montado** combinando páginas reais dos dois anteriores (não é um documento misto orgânico) — ver TAREFA-3.

---

## 2. Quadro-resumo

| ID | Prioridade | Estado | Corrigido | Commit | Testes adicionados |
|---|---|---|---|---|---|
| TAREFA-1 | máxima | VÁLIDO (reproduzido) | sim | `b540241` | 3 |
| TAREFA-2 | — | MUDANÇA DE REQUISITO | sim | `b831131` | 7 |
| TAREFA-3 | — | MUDANÇA DE REQUISITO (funcionalidade nova) | sim | `d27ebc8` | 17 |
| TAREFA-4 | — | VÁLIDO (reproduzido) — achado adicional durante a implementação | sim | `424078d` | 4 |
| TAREFA-5 | — | MUDANÇA DE REQUISITO / VÁLIDO (confirmado por inspeção) | sim | `13272cd` | 2 |
| TAREFA-6 | — | VÁLIDO (reproduzido) | sim | `2b1de1d` | 0 |
| TAREFA-7 | — | VÁLIDO (confirmado por inspeção) | sim (só registro) | `6f84fc1` | 0 |

**Contagens:**
- Válidos (reproduzidos): 4 (TAREFA-1, TAREFA-4, TAREFA-5*, TAREFA-6) — *TAREFA-5 é híbrido, ver detalhe
- Mudança de requisito: 3 (TAREFA-2, TAREFA-3, TAREFA-5*)
- Válidos (confirmados por inspeção): 1 (TAREFA-7)
- Inválidos: 0
- Corrigidos: 7 de 7
- Total de testes adicionados: **33** (12 em `test_pdf_to_md.py`, 21 em `backend/tests/`)
- Critério de conclusão da rodada (`prompt-rodada-3.md`): "as duas suítes terminam limpas" — **atendido** (ver seção 4).

---

## 3. Detalhe por item

### TAREFA-1 — Upload vai para o disco sem passar pela memória

- **Estado:** VÁLIDO (reproduzido) · prioridade máxima
- **Validação:** `tracemalloc` em volta de `_ler_com_teto()` (código anterior) processando um upload sintético de 60 MiB (gerado em disco em blocos, não um `BytesIO` gigante — simula um upload real) — pico de **~120 MiB** de memória rastreada, escalando com o tamanho do arquivo (a lista de blocos mais o resultado do `b"".join()` coexistindo brevemente).
- **Correção aplicada:** `backend/src/services/jobs.py` — novo `alocar_caminho_pdf()` gera `job_id` + caminho de destino *antes* de qualquer byte ser escrito; `criar_job(nome_original, caminho_pdf)` passou a receber um caminho **já gravado** em vez de `bytes` (sem acoplamento a FastAPI). `backend/src/routes/api.py` — `_gravar_upload_com_teto()` (substituindo `_ler_com_teto()`) grava direto em disco em blocos de 1 MiB, checando o teto **durante** a escrita — ultrapassou, aborta e apaga o arquivo parcial na hora, sem nunca acumular o restante em memória.
- **Validação da correção:** mesmo cenário via `tracemalloc` — pico caiu para **~2,4 MiB** para o mesmo upload de 60 MiB. Verificado também num servidor real rodando: RSS medido a cada 20ms via `/proc/<pid>/status` durante um `POST /api/jobs` real de 90 MiB — pico de ~56,6 MB, ~5,7 MB acima da baseline ociosa (~50,9 MB), nada perto dos 90 MB do arquivo. Um upload de 120 MiB acima do teto (então em 100 MiB) foi rejeitado com pico de RSS igualmente baixo e nenhum arquivo residual em `uploads/`.
- **Testes de regressão:** `TestGravacaoDeUploadEmDisco` (3 testes: pico de memória bem abaixo do tamanho do arquivo, `tamanho_bytes` do Job vem do arquivo real gravado, teto excedido não deixa resíduo) em `backend/tests/test_app.py`. As ~19 chamadas existentes de `jobs.criar_job(nome, bytes, diretorio=...)` em `backend/tests/test_jobs.py` foram atualizadas via um novo helper de teste `_escrever_e_criar_job()` que reproduz o "escreve bytes + registra" antigo num só passo.
- **Risco residual:** nenhum identificado — a escrita em blocos e a checagem de teto durante a escrita cobrem o caso central que a tarefa descreve.

### TAREFA-2 — Tetos e aviso redimensionados para documentos reais

- **Estado:** MUDANÇA DE REQUISITO
- **Validação do comportamento atual (antes de mudar):** `MAX_UPLOAD_BYTES` (100 MiB) e `MAX_UPLOAD_PAGES` (500) confirmados como os valores ativos antes da mudança — ambos rejeitariam os documentos reais desta rodada (`teste.pdf`, 1310 páginas e ~42 MB de um arquivo típico do "caso de uso central" descrito no prompt já excedendo o teto de páginas por 2,6×). Mensagem de rejeição de páginas confirmada como `"N paginas excede --max-pages M"` — referenciando só a flag da CLI, incorreta/confusa vinda da app web.
- **Correção aplicada:**
  - (a) `MAX_UPLOAD_BYTES`: 100 MiB → 500.000.000 bytes (500 MB decimais, valor pedido pelo usuário), continua configurável por env var. Seguro agora que a TAREFA-1 grava em disco em blocos em vez de bufferizar.
  - (b) `MAX_UPLOAD_PAGES`: 500 → 10000 (`motor_pool.py`). Deixa de ser política de uso e passa a ser proteção contra entrada patológica. `pdf_to_md.py`'s `converter_arquivo()` — mensagem reescrita para citar **os dois** ajustes (`--max-pages` na CLI, `MAX_UPLOAD_PAGES` na app web) e se descrever como "teto de proteção", já que a função não sabe qual interface a chamou.
  - (c) `Job.to_dict()` ganhou `estimativa_segundos` (paginas × EMA) e `estimativa_baixa_confianca` (contador de amostras da EMA), sempre presentes quando `paginas_totais` é conhecido — automaticamente propagados por `GET`/`POST /api/jobs`, já que os dois já serializam via `to_dict()`. Novo `AVISO_ESTIMATIVA_MINUTOS` (padrão 30, env-configurável) exposto ao frontend via `aviso_estimativa_minutos` no envelope de `GET /api/jobs` (JS não lê env var do servidor). Frontend: estimativa sempre visível na linha do job (`~38 min`, ou "(estimativa inicial)" com baixa confiança); banner de aviso (`#estimativa-alerta`, mesmo padrão do BUG-20 — nunca `window.alert`) só quando algum job da fila ultrapassa o limiar.
- **Armadilha evitada:** `max_pages` permanece **ativo** (nunca `None`) — só o valor padrão mudou. `TestMaxPages.test_pdf_corrompido_com_max_pages_ativo_e_bloqueado_pela_pre_checagem` (proteção do BUG-04) rodou **sem alteração** e continua passando, confirmando que a proteção sobreviveu.
- **Validação da correção:** servidor real — `GET /api/jobs` retorna `aviso_estimativa_minutos: 30`; um upload real retorna `estimativa_segundos`/`estimativa_baixa_confianca` preenchidos.
- **Testes de regressão:** `TestMaxPages.test_mensagem_de_excesso_nomeia_os_dois_ajustes` (`test_pdf_to_md.py`); `TestProgressoDict` (3 novos testes de estimativa), `TestApp.test_listar_jobs_expoe_o_limiar_de_aviso_de_estimativa`, `TestCriarJobsEndpoint.test_criados_incluem_estimativa_de_duracao` (`backend/tests/`).
- **Risco residual:** os novos padrões (500 MB / 10000 páginas / 30 min) são pontos de partida, não medições exaustivas — ajustáveis por variável de ambiente.

### TAREFA-3 — Detectar camada de texto e decidir OCR por job

- **Estado:** MUDANÇA DE REQUISITO (funcionalidade nova)
- **Desenho:** `pdf_to_md.tem_camada_de_texto(pdf)` — amostra até 12 páginas distribuídas ao longo do documento (não as primeiras N) e decide pela **proporção** de páginas amostradas com conteúdo substantivo (≥ 40 caracteres), não por "qualquer página com texto" — tolera páginas legitimamente sem texto num nativo. Limiar de maioria (≥ 50%), deliberadamente conservador a favor de **rodar** OCR quando incerto: falso negativo (OCR num nativo) só desperdiça tempo; falso positivo (pular OCR num digitalizado) perde conteúdo silenciosamente.
- **Calibração contra documentos reais** (não sintéticos, conforme pedido):
  - `teste.pdf` (nativo, 1310 páginas): texto rico (1300-2700+ caracteres) em **todas** as páginas de uma amostra aleatória de 10 → `tem_camada_de_texto() == True`.
  - `LGPD.pdf` (digitalizado, 26 páginas): **zero** texto extraível em **todas** as 26 páginas (verificado exaustivamente) → `tem_camada_de_texto() == False`.
  - Documento "misto": não havia um terceiro documento misto orgânico disponível — montado combinando páginas reais dos dois anteriores via `pypdfium2.PdfDocument.import_pages()` (não sintético/gerado do zero, mas também não orgânico). 60% nativo (40 páginas reais do miolo do manual) + apêndice digitalizado real (as 26 páginas do LGPD.pdf) → detectado como `True` (nativo), confirmando exatamente o modo de falha conhecido documentado. Invertendo a proporção (minoria nativa, 15 páginas, + as mesmas 26 digitalizadas) → detectado corretamente como `False`.
- **Correção aplicada:** `Job` ganhou `ocr: bool` e `ocr_origem: "detectado" | "forcado"`, decididos em `criar_job(modo_ocr=...)` via novo `_decidir_ocr()`. Seletor de 3 estados na UI (`automático`/`sempre`/`nunca`, radio buttons) enviado como campo `modo_ocr` no `POST /api/jobs` (`Form`, FastAPI) — valor desconhecido/ausente cai para "automatico" em vez de rejeitar a requisição. Linha do job mostra `OCR: sim (detectado)` / `OCR: não (forçado)`. Na dúvida (`PdfIlegivel`), o padrão é rodar OCR (mesma lógica conservadora do limiar).
- **Validação da correção:** servidor real — `LGPD.pdf` com `modo_ocr=automatico` → `ocr: true, ocr_origem: "detectado"`; mesmo arquivo com `modo_ocr=nunca` → `ocr: false, ocr_origem: "forcado"`.
- **Modo de falha conhecido, documentado (não corrigido nesta rodada, por instrução explícita):** um manual nativo com apêndice digitalizado é detectado como nativo (maioria das páginas amostradas tem texto), OCR desligado para o documento inteiro, apêndice sai vazio — documentado em `README.md` (nova seção "OCR automático por documento") e `docs/backend.md`. É exatamente para esse caso que o override `sempre` existe.
- **Testes de regressão:** `TestDeteccaoOcr` (7 testes, incluindo amostragem distribuída e `PdfIlegivel`) em `test_pdf_to_md.py`; `TestCriarJobDecisaoOcr` (6 testes) em `backend/tests/test_jobs.py`; 3 testes de wiring da rota (`modo_ocr` padrão/forçado/desconhecido) em `backend/tests/test_app.py`; 1 teste de conteúdo estático do frontend.
- **Divergência descoberta durante a implementação, corrigida na TAREFA-4 (ver abaixo):** `_processar()` já montava um `Config` por job com `ocr=job.ocr`, mas `converter_arquivo()` nunca repassava esse `cfg` para `motor.converter(pdf)` — o override não tinha efeito algum na prática. A caracterização exata desse problema no commit original da TAREFA-3 ("só tem efeito completo no motor simples") estava **imprecisa** — o efeito era zero para os dois motores, não parcial para o Docling. Corrigido e a descrição ajustada na TAREFA-4.
- **Risco residual:** nenhum além do modo de falha conhecido, já documentado como aceito.

### TAREFA-4 — `motor_pool` passa a manter dois converters

- **Estado:** VÁLIDO (reproduzido) — a tarefa em si é design/implementação nova (dependente da TAREFA-3), mas a implementação revelou um bug real e reproduzível na integração da TAREFA-3.
- **Validação:** confirmado no fonte real do `docling` 2.123.1 instalado (não presumido) — `do_ocr` é campo de `PdfPipelineOptions`, fixado na construção do `DocumentConverter`, não argumento de `convert()`; `MotorDocling._obter_converter()` cacheava um único `self._conv`, construído com `self.cfg.ocr` fixado desde `__init__`. **Achado adicional, reproduzido:** `converter_arquivo()` nunca repassava `cfg` para `motor.converter(pdf)` — chamado só com `pdf`. O override por job da TAREFA-3 tinha efeito **zero**, confirmado lendo o código (não "primeiro job vence", literalmente nenhum job alcançava o motor com seu próprio `ocr`).
- **Correção aplicada:** `MotorDocling.__init__` passou a guardar `self._convs: dict[bool, DocumentConverter]` em vez de um único `self._conv` — `_obter_converter(ocr)` cacheia um converter por modo, ambos preguiçosos (nenhum carrega no startup). `MotorBase.converter()` (e as duas implementações) ganharam um parâmetro `*, ocr: bool | None = None` — `None` cai para `self.cfg.ocr` (compatível com toda chamada direta existente, CLI incluída); um valor explícito sobrepõe só para aquela chamada. `converter_arquivo()` passou a chamar `motor.converter(pdf, ocr=cfg.ocr)` explicitamente — para a CLI isso não muda nada observável (o `cfg` ali é o mesmo objeto usado pra construir o motor); para a app web, é o que faz o override por job da TAREFA-3 valer de verdade.
- **Validação da correção:** verificado ao vivo com Docling real — duas chamadas sequenciais `motor.converter(pdf, ocr=False)` / `motor.converter(pdf, ocr=True)` produziram dois `DocumentConverter` distintos em `self._convs`; uma terceira chamada repetindo um modo já visto **não** criou um novo (reaproveitamento confirmado). Um teste de ponta a ponta real via servidor (dois jobs, um `modo_ocr=sempre` e outro `modo_ocr=nunca`, no **mesmo** processo) concluiu os dois corretamente com `ocr`/`ocr_origem` certos.
- **Medição de RSS obrigatória** (Python 3.12.3, docling 2.123.1, 62 GiB RAM, processo real via `/proc/<pid>/status`):

  | Cenário | RSS |
  |---|---|
  | (1) Nenhum converter carregado | ~51 MB |
  | (2) Só o converter **com** OCR | ~1,49 GB |
  | (3) Só o converter **sem** OCR | ~1,41 GB |
  | (4) Os dois carregados (mesmo processo) | ~1,88 GB |

  (4) é bem menor que a soma ingênua de (2)+(3) (~2,9 GB) — os dois modos compartilham memória de baixo nível; dado relevante para uma futura decisão de pool de processos.

  **Durante a conversão de um documento real** (20 e 50 páginas reais extraídas do mesmo manual de 1310 páginas, sem OCR): RSS **não** cresce de forma pequena e linear por página — explode para dezenas de GB assim que a conversão começa, num patamar já quase todo presente com só 20 páginas:

  | Páginas | Pico de RSS | RSS após concluir |
  |---|---|---|
  | 20 | ~17,7 GB | ~14,0 GB |
  | 50 | ~19,6 GB | ~15,1 GB |

  **Divergência registrada:** a tarefa pede a medição "durante a conversão de um documento grande" — extrapolar os dois pontos acima para as 1310 páginas completas (não linear, dominado por um patamar alto) seria uma aposta arriscada demais para rodar sem supervisão nesta máquina compartilhada de 62 GiB — risco real de OOM que afetaria outros processos da máquina, não só o teste. **As 1310 páginas completas não foram processadas.** Os dois pontos parciais já estabelecem o fato mais importante para uma decisão futura: o custo é dominado por um patamar de dezenas de GB, não por um crescimento pequeno e previsível por página.
- **Testes de regressão:** `TestMotorDocling.test_um_converter_por_modo_de_ocr_cacheado_separadamente`, `test_converter_sem_ocr_explicito_usa_o_padrao_do_cfg` (`test_pdf_to_md.py`, via stub Docling); `TestConversaoUnitaria.test_converter_arquivo_repassa_cfg_ocr_ao_motor`; `TestFilaProcessamentoOk.test_processar_repassa_o_ocr_do_job_ao_motor` (`backend/tests/test_jobs.py`, ponta a ponta pelo worker real). Corrigidos 3 stubs de teste com assinatura antiga (`MotorFake`, `_MotorDeTeste`, um stub inline) e uma asserção que referenciava o atributo renomeado (`_conv` → `_convs`).
- **Risco residual:** o custo de memória por documento real (não por modo) é alto o bastante para levantar dúvida sobre processar documentos de milhares de páginas num único processo sem algum tipo de particionamento — fora do escopo desta rodada (nenhum paralelismo/pool foi implementado, por restrição explícita), mas é o dado que uma decisão futura precisa.

### TAREFA-5 — Estimativa de progresso por modo

- **Estado:** híbrido — MUDANÇA DE REQUISITO (a EMA por modo é uma melhoria de design, não a correção de um defeito de comportamento observável antes da TAREFA-3/4 existir) e VÁLIDO (confirmado por inspeção) para o problema em si (misturar OCR/sem-OCR numa única EMA global é um defeito de design inspecionável diretamente no código, sem precisar de execução).
- **Validação:** por inspeção do código anterior — `_segundos_por_pagina`/`_amostras_ema` eram valores globais únicos; após a TAREFA-3/4, jobs com e sem OCR (custo por página em ordens de grandeza diferentes) alimentariam a mesma média, que não convergiria para nada útil para nenhum dos dois.
- **Correção aplicada:** `_segundos_por_pagina`/`_amostras_ema` viraram `dict[bool, ...]`, chaveados pelo `job.ocr` efetivo (não pelo seletor "automatico"/"sempre"/"nunca"). `Job.to_dict()` lê a entrada do próprio modo do job. As duas EMAs começam no mesmo palpite inicial (2,0 s/página) — nenhuma medição desta rodada justificaria valores iniciais diferentes sem calibrar no vazio.
- **Validação da correção:** testes unitários confirmando que atualizar a EMA de um modo não afeta o outro; teste de ponta a ponta via worker real (`TestFilaAtualizaProgresso`, pré-existente) continua passando, confirmando a cadeia completa (`criar_job` → `_processar` → `_atualizar_estimativa` → `to_dict`) funcionando com o novo formato.
- **Decisão avaliada e registrada** (a tarefa pedia avaliação, não necessariamente implementação): usar "a taxa observada do próprio documento" em vez da EMA por modo para a projeção `pagina_estimada` dentro de um job em andamento **não é implementável nesta rodada** — o Docling não expõe nenhum callback de progresso por página (já documentado em `architecture.md`/`code.md`), então não há sinal intermediário durante a conversão de UM job para calibrar contra; só a duração total, uma vez, no fim. A EMA por modo já é a melhoria alcançável com o sinal que existe hoje; refinar mais seria especular sem dado real. Fica para uma rodada futura, condicionado a alguma fonte de sinal intermediário.
- **Testes de regressão:** `TestProgressoDict.test_ema_e_independente_por_modo_de_ocr`, `TestAtualizarEstimativa.test_atualizar_um_modo_nao_afeta_o_outro` (`backend/tests/test_jobs.py`). Três fixtures existentes corrigidas para `.copy()` os dicts ao salvar (uma atribuição simples compartilharia o mesmo dict entre "original" e "atual", corrompendo o restore).
- **Risco residual:** nenhum novo — a limitação de não ter sinal intermediário por página é do Docling, não desta implementação.

### TAREFA-6 — Tirar contagens de teste da documentação

- **Estado:** VÁLIDO (reproduzido)
- **Validação:** `README.md`/`architecture.md` diziam `87`/`71` testes (fixado no primeiro commit da rodada 2, BUG-24); a contagem real no momento desta verificação era `99`/`94` — divergência confirmada rodando as duas suítes.
- **Correção aplicada:** **não** foi atualizar o número de novo (mesma armadilha, um ciclo depois) — as contagens foram **removidas** da prosa nos dois arquivos, substituídas por descrição de estrutura ("suíte do motor de conversão / CLI", "duas suítes independentes"). Nenhuma outra menção de contagem de teste foi encontrada em `docs/*.md`.
- **Validação da correção:** grep confirma zero ocorrências de contagens de teste em `README.md`/`docs/*.md` após a mudança.
- **Testes de regressão:** nenhum — mudança de prosa de documentação, não de comportamento.
- **Risco residual:** nenhum.

### TAREFA-7 — Corrigir a seção "fora do escopo" do `bug_report-2.md`

- **Estado:** VÁLIDO (confirmado por inspeção) — a contradição é lida diretamente no próprio documento (seção 5 dizia "Nenhum" contradizendo o detalhe do BUG-24 no mesmo arquivo), sem precisar de execução para confirmar.
- **Validação:** leitura cruzada da seção 5 (`### Problemas encontrados durante a execução que não constam em bugs-2.md` → "Nenhum") contra o detalhe do BUG-24 (mesmo documento), que registra ter encontrado e corrigido contagens de teste desatualizadas em `README.md`/`architecture.md` — achado fora dos 4 itens de `bugs-2.md`.
- **Correção aplicada:** **só o registro** — seção 5 de `bug_report-2.md` reescrita para descrever o achado do BUG-24 corretamente, com uma nota de errata inline (mesmo padrão usado pelas correções de BUG-11/BUG-21 na própria rodada 2). Nenhum código alterado.
- **Validação da correção:** leitura da seção corrigida — não há mais contradição com o detalhe do BUG-24.
- **Testes de regressão:** nenhum (mudança de documentação de relatório, não de comportamento).
- **Risco residual:** nenhum.

---

## 4. Estado das suítes

Capturado via `git worktree` no commit inicial desta rodada (`4cce636`) e no HEAD final (`6f84fc1`), mesmo `.venv`.

### Antes (`4cce636` — fim da rodada 2)

```
$ python -m unittest test_pdf_to_md
Ran 87 tests in 2.410s
OK

$ pytest backend/
73 passed, 11 subtests passed in 3.13s
```

### Depois (`6f84fc1`, HEAD)

```
$ python -m unittest test_pdf_to_md
Ran 99 tests in 2.253s
OK

$ pytest backend/
94 passed, 15 subtests passed in 3.45s
```

**Critério de conclusão da rodada atendido:** as duas suítes terminam limpas (`OK`/todos os testes passando) antes e depois — nenhuma regressão introduzida, e as duas já estavam limpas ao entrar nesta rodada (herdado da correção do BUG-24 na rodada 2).

**Delta:** `test_pdf_to_md.py` +12 (87→99); `backend/tests/` +21 (73→94, +4 subtests). Total +33, batendo com a soma da coluna "Testes adicionados" do quadro-resumo.

---

## 5. Pendências

Nenhum item desta rodada ficou com código pendente — os 7 (TAREFA-1 a TAREFA-7) foram corrigidos/implementados integralmente, e o critério de conclusão (as duas suítes limpas) foi atendido.

### Decisões registradas, não é bug/ação desta rodada

- **TAREFA-4 — documento de 1310 páginas não processado por inteiro.** Risco real de OOM numa máquina compartilhada de 62 GiB, extrapolando de dois pontos parciais (20 e 50 páginas) que já indicam custo dominado por um patamar de dezenas de GB, não crescimento linear pequeno por página. Os dois pontos parciais ficam registrados como o dado disponível para uma decisão futura de pool de processos — processar o documento completo, se necessário, deveria acontecer com supervisão de memória (cgroups/limites de processo) numa rodada dedicada a paralelismo, não como efeito colateral de uma medição.
- **TAREFA-5 — projeção `pagina_estimada` continua linear pelo tempo decorrido**, não pela taxa observada do próprio documento. Bloqueado por uma limitação real do Docling (sem callback de progresso por página), não por uma escolha desta rodada — registrado para reavaliação se uma versão futura do Docling expuser esse sinal.
- **TAREFA-3 — modo de falha conhecido do documento misto** (manual nativo + apêndice digitalizado detectado como nativo, OCR desligado pro documento inteiro) permanece sem correção automática — documentado em `README.md`/`docs/backend.md`, resolvido manualmente pelo override `sempre` quando o usuário souber que o documento é misto. Detecção por página, não por documento, ficou explicitamente fora do escopo desta rodada.
- **Restrições gerais da rodada respeitadas:** nenhum código de isolamento por usuário, autenticação, cookies de sessão ou `HOST` foi tocado; nenhum paralelismo de jobs, pool de processos ou botão de parar foi implementado — a TAREFA-4 só mediu RAM, como pedido.

### Problemas encontrados durante a execução que não constam em `prompt-rodada-3.md`

1. **`converter_arquivo()` nunca repassava `cfg` para `motor.converter(pdf)`** (achado durante a implementação da TAREFA-4, mas a raiz do problema já existia desde antes da TAREFA-3 tentar usá-lo) — o override de OCR por job da TAREFA-3 tinha efeito zero até essa correção. Detalhado na TAREFA-4 acima; commit `424078d`.
2. **Caracterização imprecisa no commit original da TAREFA-3**: a mensagem de commit da TAREFA-3 descreveu a limitação acima como "efeito completo só no motor simples" — impreciso; o efeito era zero para os dois motores. Corrigido nesta rodada como parte da implementação da TAREFA-4 (não como um item avulso), com a descrição correta documentada em `docs/backend.md`.
3. **`teste.pdf` na raiz do repositório**: um documento real de 1310 páginas apareceu no filesystem entre o fim da rodada 2 e o início desta, fora de qualquer ação deste agente — usado para calibração real da TAREFA-3/TAREFA-4 (não commitado; ver seção 1).

Nenhum outro problema fora do escopo de `prompt-rodada-3.md` foi procurado ativamente além do necessário para implementar os 7 itens.
