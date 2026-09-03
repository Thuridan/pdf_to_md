# bug_report-5.md — quinta rodada (execução de `prompt-rodada-5.md`)

## 1. Cabeçalho

- **Data de execução:** 2026-09-03
- **Commit inicial:** `419a74f` (Add bug_report-4.md for the prompt-rodada-4.md work — TAREFA-1, TAREFA-4)
- **Commit final:** `1abe447` (Measure the impact of table span flattening in Markdown export — TAREFA-8)
- **Sistema operacional:** Ubuntu 24.04.4 LTS (x86_64), 62 GiB de RAM, sem GPU NVIDIA — mesma máquina das rodadas anteriores
- **Ambiente de validação:**
  - Python 3.12.3
  - `docling` 2.123.1 · `docling_core` 2.92.0
  - `pypdfium2` 5.13.0
  - `fastapi` 0.141.1 · `starlette` 1.6.0 · `uvicorn` 0.52.4 · `httpx2` 2.12.0
  - `setuptools` 84.0.0
  - Idêntico às rodadas anteriores — nenhuma dependência nova instalada. `docling-serve`/`docling-jobkit` (TAREFA-7) **não** foram instalados — investigados via `docling.service_client` (já embutido no `docling` local) e via leitura do código-fonte público (GitHub, `gh api`), por instrução explícita de não instalar.

**Antes de começar:** os commits das rodadas 3 e 4 já estavam no `origin` (push feito na sessão anterior, confirmado por `git status` antes de iniciar) — a instrução do prompt para fazer push primeiro já estava satisfeita.

**Documento real usado para as medições desta rodada:** `teste.pdf` (~42 MB, 1310 páginas, manual real de administração NGFW) — mesmo arquivo das rodadas 2–4, só no filesystem local, não commitado. Extratos usados: a mesma faixa de 46 páginas (50–95) das rodadas anteriores (TAREFA-1, TAREFA-2, TAREFA-3, parte da TAREFA-8); páginas 61 e 91 isoladas em PDFs de 1 página (TAREFA-5); uma amostra de 70 páginas espalhada pelo documento inteiro, 5 a cada 100 (TAREFA-8, tabelas).

---

## 2. Quadro-resumo

| ID | Bloco | Estado | Executado | Commit | Testes adicionados |
|---|---|---|---|---|---|
| TAREFA-1 | A (implementação) | VÁLIDO (implementado) | sim | `cc4b1ac` | 6 |
| TAREFA-2 | A | VÁLIDO (implementado) | sim | `ac30f68` | 6 |
| TAREFA-3 | A | VÁLIDO (implementado) | sim | `cd30a3a` | 11 |
| TAREFA-4 | A | VÁLIDO (implementado) | sim | `233b607` | 5 |
| TAREFA-5 | B (medição) | **ACHADO MAIOR** — refuta a causa citada no `prompt-rodada-4.md` e no `prompt-rodada-5.md` | sim — medição + registro | `5cca01f` | 0 |
| TAREFA-6 | B | Correção aplicada, **com divergência registrada** do texto sugerido pelo prompt | sim — só registro | `a21746f` | 0 |
| TAREFA-7 | B | Investigado e registrado, não adotado | sim — só registro | `837d39f` | 0 |
| TAREFA-8 | B | Medido e registrado, proporção baixa (4,8%) mas efeito por ocorrência sério | sim — só registro | `1abe447` | 0 |

**Contagens:**
- Implementadas com teste de regressão: 4 (TAREFA-1 a 4)
- Medição/registro sem mudança de comportamento: 4 (TAREFA-5 a 8)
- Achado que reverte a conclusão de uma rodada anterior: 1 (TAREFA-5, propagado para TAREFA-6)
- Total de testes adicionados: **28** (22 em `test_pdf_to_md.py`, 6 em `backend/tests/test_jobs.py`)
- Critério de conclusão da rodada (`prompt-rodada-5.md`): "as duas suítes terminam limpas" — **atendido** (ver seção 4).
- Restrição "não altere o comportamento padrão do OCR" — **respeitada**: `tem_camada_de_texto()` não foi tocada, OCR continua desligado por padrão na detecção automática, `traverse_pictures=True` (achado da TAREFA-5) **não foi implementado** em código algum, só medido e registrado.

---

## 3. Detalhe por item

### Bloco A — implementação

### TAREFA-1 — Marcadores de página na saída

- **Estado:** implementado.
- **Verificação prévia:** `export_to_markdown()` do `docling_core` aceita `page_break_placeholder`, mas **não interpola o número da página** — confirmado lendo `docling_core/transforms/serializer/markdown.py`: o marcador interno de quebra carrega `prev_page`/`next_page`, mas `serialize_doc()` descarta os dois e usa só a string literal de `params.page_break_placeholder`, a mesma em toda quebra.
- **Correção aplicada:** `pdf_to_md.py` — sentinela interna (`_SENTINELA_QUEBRA_DE_PAGINA`, nunca aparece no `.md` final) passada a `export_to_markdown()`; `_numerar_paginas()` substitui as quebras por `<!-- página N -->` numerados sequencialmente (as quebras aparecem sempre em ordem crescente de página, então a N-ésima quebra é sempre a transição pra página N+1) e marca também o início da página 1 (que o `docling_core` não marca sozinho). `MotorSimples` alinhado ao mesmo formato de marcador, com o número real da página (páginas sem texto extraível não geram marcador — mesmo comportamento observado no Docling, que só marca transições entre páginas com conteúdo serializado).
- **Validação da correção:** extrato real de 46 páginas (`teste.pdf`, 50–95) — 46 marcadores, sequenciais, sem furo, **nos dois motores**. Nenhum marcador caiu no meio de uma linha de tabela (`|...|`) ou dentro de um bloco de código (```` ``` ````, o documento real tem 10 blocos na faixa) — verificado por varredura programática, não só inspeção visual.
- **Testes de regressão:** `TestNumerarPaginas` (4 testes, função pura, sem docling) + 2 testes de fiação em `TestMotorDocling` (pede `page_break_placeholder` ao Docling; numera a partir da sentinela devolvida pelo stub) — 6 no total.
- **Risco residual:** nenhum identificado.

### TAREFA-2 — Suprimir placeholders de imagem decorativa

- **Estado:** implementado.
- **Abordagem:** `PictureSerializer` customizado (subclasse de `MarkdownPictureSerializer` do `docling_core`), plugado via `MarkdownDocSerializer(picture_serializer=...)` — a via de baixo nível, já que `export_to_markdown()` não expõe como trocar o serializador de figura. Emite string vazia para figuras com bbox (`item.prov[0].bbox`, área em pts²) abaixo de `cfg.limiar_imagem_pt2` (`--min-image-area`, padrão 1000.0, `0` desliga), delega ao padrão para as demais.
- **Divergência do prompt, registrada:** o limiar original sugerido (~120.000 **pixels** do bitmap nativo) não foi usado — `PictureItem` só expõe o tamanho do bitmap com `generate_picture_images=True` (opção cara em memória, avaliada e adiada na rodada 4, TAREFA-4). Recalibrado numa métrica diferente, sempre disponível sem esse custo: área de bbox de layout em pts² (equivalente a pixels @72dpi). Calibração no mesmo extrato real de 46 páginas: separação bimodal limpa — 39 ícones entre 63–424 pts², 26 capturas reais entre 2.518–105.813 pts², vão de ~6× entre os dois grupos. `1000` cai no meio do vão.
- **Validação da correção:** as duas capturas que motivaram a rodada 4 (páginas 61 e 91, 102.988 e 64.948 pts²) confirmadas preservadas acima do limiar. Contagem de `<!-- image -->` na faixa de 46 páginas: 65 sem supressão → 26 com supressão (39 suprimidos, batendo exatamente com a contagem direta de ícones).
- **Testes de regressão:** `TestSupressaoDeIconesDecorativos` (4 testes, `DoclingDocument` real construído via API do `docling_core`, não o stub de `docling.*`) + 2 testes do flag CLI — 6 no total.
- **Risco residual:** o limiar (1000 pts²) é calibrado contra um documento só, como o original de 120.000 px era — mesma ressalva herdada, agora registrada explicitamente como divergência de métrica, não só de valor.

### TAREFA-3 — Expor os confidence grades por página

- **Estado:** implementado.
- **Desenho:** `MotorBase.converter()` passou a devolver `ResultadoMotor` (markdown + `grau_medio` + `paginas_grau_baixo`) em vez de só a string. `_extrair_confianca()` lê **só os graus** (`mean_grade`) do `ConfidenceReport` do Docling — nunca os escores numéricos, seguindo a recomendação da própria documentação do Docling (o cálculo/ponderação pode mudar). "Página com grau baixo" = `mean_grade` da página abaixo de `GOOD` (`POOR`/`FAIR`). `confidence.pages` confirmado 1-indexado pelo número real da página (chaves `1..46` no extrato de 46 páginas) — mesmo índice de `_marcador_pagina()` da TAREFA-1, então os dois recursos se complementam diretamente (usuário vai da lista de páginas com grau baixo direto ao marcador certo no `.md`).
- **Correção aplicada:** `converter_arquivo()` repassa os dois campos a `Resultado`; `jobs._processar()` copia para `Job.grau_medio`/`Job.paginas_grau_baixo`, expostos por `to_dict()`. CLI: aviso de `partial_success` agora inclui o grau; novo aviso separado quando o grau é baixo **mesmo com status de sucesso completo** (o caso de uso que a doc do Docling documenta — identificar documentos que precisam de revisão manual — não é coberto só pelo status). Grau baixo **nunca** vira erro. Frontend: linha do job mostra o grau (`RUBRICA_GRAU`: `poor`→"ruim", `fair`→"razoável", `good`→"boa", `excellent`→"excelente") e lista as páginas com grau baixo, quando existirem.
- **Validação da correção:** ao vivo, servidor real rodando — upload do extrato de 46 páginas via `POST /api/jobs`, aguardado até `concluido`: `grau_medio: "excellent"`, `paginas_grau_baixo: [39]`, batendo exatamente com a medição direta contra o mesmo arquivo (`mean_score` ≈ 0,92; distribuição por página: 38 excellent, 7 good, 1 fair).
- **Testes de regressão:** `TestExtrairConfianca` (4) + `TestConfiancaNoMotorDocling` (4) em `test_pdf_to_md.py`; `TestGrauDeConfianca` (1) + `TestSemGrauDeConfianca` (1) em `backend/tests/test_jobs.py`; 1 teste de conteúdo estático do frontend — 11 no total.
- **Risco residual:** nenhum identificado — `table_score` ainda não está implementado na versão instalada do Docling (`NaN`, ignorado no `nanmean`/`nanquantile` do agregado), então o grau hoje reflete só `layout`/`ocr`/`parse`; se uma versão futura do Docling implementar `table_score`, o grau passa a refletir mais um componente automaticamente, sem mudança de código aqui.

### TAREFA-4 — Retomada de lote no backend

- **Estado:** implementado.
- **Correção aplicada:** `jobs.retomar_jobs_do_disco()`, chamado no `lifespan()` do FastAPI antes de `iniciar_worker()`. Varre `uploads/*.pdf`; par completo (`{job_id}.pdf` + `{job_id}.md`) vira `Job` com `status="concluido"`; `.pdf` sem `.md` correspondente fica de fora, **não reenfileirado automaticamente** (instrução explícita — não há como saber se parou por falha ou por interrupção), só contado e logado. Varredura por idade **não implementada** (rodada de isolamento, fora de escopo, por instrução).
- **Limitações registradas** (nome_original e ocr_origem não recuperáveis do disco): job retomado usa o nome do arquivo em disco como nome exibido; `ocr_origem` vira `"desconhecido"` em vez de um valor inventado — o frontend suprime o rótulo de OCR nesse caso, em vez de mostrar `"detectado"`/`"forçado"` como se fosse fato. Persistir um `.json` de metadados ao lado do PDF resolveria os dois, avaliado e descartado por ser mudança maior, desproporcional ao problema — registrado como opção futura.
- **Validação da correção:** ao vivo — um par completo e um `.pdf` órfão colocados manualmente em `uploads/`, servidor real reiniciado: `GET /api/jobs` mostrou o par como `concluido` com `ocr_origem: "desconhecido"`, download funcionando (`GET .../download` devolveu o conteúdo certo); o órfão não apareceu na lista.
- **Testes de regressão:** `TestRetomarJobsDoDisco` (4 testes) em `backend/tests/test_jobs.py` + 1 teste de conteúdo estático do frontend — 5 no total.
- **Risco residual:** nenhum identificado dentro do escopo definido. O log de retomada (`LOG.info`) não aparece no `uvicorn.log` por padrão — confirmado ao vivo que isso é um comportamento **pré-existente** do projeto (nenhuma configuração de `logging.basicConfig` no backend, então o root logger fica em `WARNING`), não uma regressão desta tarefa; não corrigido, por estar fora do escopo desta rodada.

### Bloco B — medição e registro

### TAREFA-5 — Matriz de OCR: idioma, escala e motor

- **Estado:** as duas hipóteses testáveis (idioma, escala) foram refutadas; a terceira (motor alternativo) não foi testável neste ambiente; a investigação de por que nada mudava nada encontrou a causa real, que não estava nas três hipóteses do prompt.
- **(a) Idioma — refutado.** `lang=["pt"]` e `lang=["en"]` produziram saída **byte a byte idêntica** nas páginas 61 e 91. O log do RapidOCR mostra por quê: os dois carregam o mesmo arquivo de modelo de reconhecimento (`PP-OCRv6_rec_small.onnx`) — no tier "small" instalado, `lang` não troca de modelo entre português e inglês. A premissa do prompt não se confirmou para este tier.
- **(b) Escala — refutado.** `scale` 1.0/3.0/6.0 produziu a mesma saída idêntica.
- **(c) Motor alternativo — não testável neste ambiente.** Sem GPU NVIDIA (`nvidia-smi` ausente): Nemotron-OCR fora de alcance. Tesseract não instalado e sem `sudo` sem senha disponível: não testado. Registrado como bloqueio de ambiente, não como resultado.
- **O achado real:** mesmo com `force_full_page_ocr=True` (bypassa o filtro `PDF_AWARE_LAYOUT_REGIONS` por completo) o resultado continuou idêntico — sinal de que o problema não era o OCR rodar na região errada. Isso levou a inspecionar `export_to_markdown()` mais a fundo: tem um parâmetro `traverse_pictures: bool = False`, documentado como necessário para "scanned/image-based PDFs processed with full-page OCR, where the layout model places all OCR text as children of a top-level PictureItem" — exatamente este caso, nunca usado pelo projeto. Reexportar o **mesmo** `ConversionResult` já convertido, só com `traverse_pictures=True`, recupera **os cinco termos-alvo nas duas páginas**: `scp_admin`, `password-complexity`, `update-server` (página 61); `Max Rows in CSV Export`, `corp-syslog` (página 91).
- **Custo do achado, se um dia virar comportamento padrão:** desprezível — ~6ms → ~10ms por chamada de `export_to_markdown()`, porque é reserialização sobre um resultado já pronto, não reprocessamento. O custo de memória do OCR em si (2,37× de RSS, medido na rodada 4) não muda — já reflete rodar o OCR; `traverse_pictures` só decide se o texto reconhecido aparece no `.md`.
- **Formato do texto recuperado:** sequência linear de fragmentos (um por célula/linha detectada), sem preservar a estrutura visual da tabela original do XML Diff — legível e buscável, não formatado como a tabela original.
- **Correção aplicada:** nenhuma — medição e registro, por instrução explícita ("Não altere o comportamento padrão do OCR"). Documentado em `docs/backend.md` (nova seção) e como errata na seção da TAREFA-1 da rodada 4 (texto original preservado, ver TAREFA-6).
- **Testes de regressão:** nenhum — nenhum comportamento de código mudou.
- **Risco residual:** o achado foi verificado em duas páginas de um documento. `traverse_pictures=True` tem uma ressalva documentada pelo próprio Docling (texto de OCR de página inteira vira filho de `PictureItem`) que pode se comportar diferente em documentos com estruturas de página distintas — recomendável validar contra mais exemplos antes de virar padrão do projeto, o que é trabalho de uma rodada futura, não desta.

### TAREFA-6 — Corrigir o enquadramento da conclusão da rodada 4

- **Estado:** corrigido, **com divergência registrada** em relação ao texto de correção sugerido pelo `prompt-rodada-5.md`.
- **O que o prompt pedia:** reclassificar como "hipótese parcialmente confirmada" (OCR ajuda em capturas simples, falha nas densas) e apontar o custo de memória como a razão real de parar, não a ausência de benefício.
- **O que a medição da TAREFA-5 mostrou:** não é "parcial" — os dois exemplos que motivaram a rodada 4 foram **totalmente** recuperados (os cinco termos-alvo, nos dois), uma vez exportados corretamente. A causa também não é "RapidOCR não lida bem com texto denso" (a leitura original da rodada 4) — é um parâmetro de exportação nunca usado.
- **Correção aplicada:** `bug_report-4.md` — bloco de errata inserido no topo do detalhe da TAREFA-1 (texto original de medição preservado sem edição abaixo, porque a medição em si — tempo, RSS, timings — está correta; só a interpretação do resultado estava errada), quadro-resumo e contagens atualizados para refletir "confirmada" em vez de "refutada", com a razão de parada recolocada no custo de RSS (que continua real e independente de `traverse_pictures`). `docs/backend.md` (rodada 4, TAREFA-1) e `README.md` também corrigidos com a mesma errata.
- **Testes de regressão:** nenhum — só documentação.
- **Risco residual:** nenhum.

### TAREFA-7 — Avaliar `docling-serve` como motor

- **Estado:** investigado e registrado; **não instalado, não adotado**.
- **Cobertura de opções (verificado no `ConvertDocumentsOptions` real, do pacote `docling` local):** `do_ocr`, `ocr_lang`, `table_mode`/`table_cell_matching`, `page_range` (parâmetro de primeira classe em `client.convert()`, já cobrindo uma futura rodada de seleção de páginas), `md_page_break_placeholder` (a TAREFA-1 desta rodada) e `document_timeout` — cobertura completa. `--device`/`--threads` e o `backend` específico do RapidOCR — **não cobertos** pelo cliente (viram configuração do servidor, ou ficam em um campo opaco não tipado). `DoclingServiceClient.convert()` devolve o **mesmo** `docling.datamodel.document.ConversionResult` da conversão local (confirmado lendo o import em `docling/service_client/client.py`) — `_exportar_markdown()`/`_numerar_paginas()`/`_extrair_confianca()` (as três funções que este projeto já escreveu nesta rodada) funcionariam sem alteração sobre um resultado remoto; a supressão de ícones (TAREFA-2) não teria equivalente na API REST, mas sobrevive porque o cliente devolve o `DoclingDocument` real, não só markdown pronto.
- **`SHARE_MODELS` — mais limitado do que a descrição do prompt sugeria.** Lido em `docling_jobkit/orchestrators/local/worker.py` (GitHub, `docling-project/docling-jobkit`): compartilha só os pesos estáticos do modelo entre workers **do mesmo processo**. Nas medições reais deste projeto (rodada 3, TAREFA-4), um `DocumentConverter` carregado custa ~1,4–1,9 GB — não os ~22 GB por worker citados como motivação. O custo de dezenas de GB medido nas rodadas 3/4 vem do processamento de página em andamento, não do carregamento de modelo; `SHARE_MODELS` não resolve isso — dois workers convertendo documentos grandes ao mesmo tempo pagam o pico cada um, com ou sem `SHARE_MODELS`.
- **O que fica de fora de qualquer forma:** isolamento por usuário, dono de job, a UI, fila persistente/histórico de jobs (o `docling-serve` é uma API de tarefa única com expiração de resultado, não um `JobStore`) — tudo isso continua responsabilidade deste projeto.
- **Custo operacional:** um processo/container a mais pra operar; hoje o projeto é um único processo Python.
- **Decisão:** não tomada — registro para o usuário decidir, com a ressalva de que a motivação original de memória só se sustenta parcialmente.
- **Testes de regressão:** nenhum — investigação, nenhum código tocado.
- **Risco residual:** nenhum — nada foi instalado nem alterado.

### TAREFA-8 — Medir o impacto do achatamento de spans de tabela

- **Estado:** medido e registrado; formato de saída não alterado.
- **Método:** amostra real de 70 páginas espalhada pelo `teste.pdf` inteiro (5 páginas a cada 100, não um trecho contínuo) — contagem de células com `row_span > 1`/`col_span > 1` via `TableItem.data.table_cells` no `DoclingDocument` real.
- **Resultado:** 42 tabelas na amostra, 2 com pelo menos uma célula com span (**4,8%** — proporção baixa, dentro do limiar do próprio prompt para só registrar e seguir). Exemplo real inspecionado (página 10 da amostra): uma célula `colspan="2"` mesclando "Access Level" e o começo de "Description" vira, no Markdown achatado, uma célula só com `"Authentication Specifies whether the administrator can create a"` — nome do nível de acesso grudado no início da descrição real — e a coluna seguinte fica só com o resto do texto. A linha continua com o número certo de colunas; nada indica visualmente que os dados migraram de coluna.
- **Leitura:** proporção baixa por si só não vira decisão de produto imediata, mas a amostra (70/1310 páginas, 5,3%) não é exaustiva, e o efeito por ocorrência (resposta errada com aparência de certa) é sério o bastante para pesar numa decisão futura entre exportar HTML junto do `.md` ou escrever um `TableSerializer` que repita o valor nas células cobertas.
- **Correção aplicada:** nenhuma — medição e registro, por instrução explícita.
- **Testes de regressão:** nenhum — nenhum comportamento de código mudou.
- **Risco residual:** a amostra, embora espalhada pelo documento inteiro, ainda é uma fração (5,3%) — uma proporção real mais alta em outras seções não amostradas não pode ser descartada com certeza.

---

## 4. Estado das suítes antes/depois

**Antes** (commit `419a74f`, estado final da rodada 4):
```
test_pdf_to_md.py ......................................... 99 passed, 6 subtests passed
backend/tests/    ......................................... 94 passed, 15 subtests passed
```

**Depois** (commit `1abe447`, estado final desta rodada):
```
test_pdf_to_md.py ......................................... 121 passed, 6 subtests passed
backend/tests/    ......................................... 100 passed, 15 subtests passed
```

99 → 121 (+22) em `test_pdf_to_md.py`, 94 → 100 (+6) em `backend/tests/` — 28 testes novos no total, todos das quatro tarefas de implementação do Bloco A. As suítes foram rodadas depois de **cada** commit da rodada (não só no início/fim), sempre limpas antes de prosseguir para o item seguinte. Critério de conclusão da rodada ("as duas suítes terminam limpas") **atendido**.

---

## 5. Pendências

- **Achado da TAREFA-5 não implementado** (`traverse_pictures=True`): a rodada mediu e registrou, mas não ligou isso em código, por instrução explícita desta rodada. É o candidato mais óbvio de próxima ação de código relacionada a OCR — mas junto com a TAREFA-4 (rodada 4, embutir imagens) e a rodada de gestão de memória, precisa de uma decisão de produto sobre custo/formato antes de virar padrão (o texto recuperado hoje não preserva estrutura de tabela, por exemplo).
- **TAREFA-5(c), motor alternativo, não testado:** Tesseract e Nemotron-OCR continuam não avaliados neste ambiente (sem `sudo` sem senha, sem GPU NVIDIA). Se esses recursos ficarem disponíveis, vale revisitar — mas dado o achado de `traverse_pictures`, a motivação original de "trocar de motor" perdeu força: o problema nunca foi o motor de OCR.
- **`docling-serve` (TAREFA-7):** nenhuma decisão tomada, material de entrada registrado em `docs/architecture.md`. Migração continua não recomendada nem descartada — depende de decisões de produto fora do alcance desta rodada (hospedagem, isolamento por usuário, se vale abrir mão do controle fino de acelerador por processo).
- **`bug_report.md`, `bug_report-2.md`, `bug_report-3.md`** continuam apagados por decisão do usuário (rodada 4) — não tocados nesta rodada, recuperáveis via `git show` se necessário.
- **`teste.pdf`, `prompt-rodada-4.md`, `prompt-rodada-5.md`** continuam não rastreados no repositório (mesmo tratamento de rodadas anteriores) — não apagados, por não ter sido pedido.
- **Push:** os 8 commits desta rodada (`cc4b1ac` até `1abe447`) ainda não foram confirmados como enviados a `origin/main` — nenhuma ação de push foi pedida ainda para este trabalho.
- **TAREFA-8:** amostra de 70/1310 páginas — se uma amostra maior (ou a conversão completa, cara em tempo/memória) mostrar proporção mais alta de tabelas com span, a leitura "proporção baixa" pode não se sustentar; não há plano de reamostragem agendado.
