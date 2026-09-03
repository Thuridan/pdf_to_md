# bug_report-4.md — quarta rodada (execução de `prompt-rodada-4.md`)

## 1. Cabeçalho

- **Data de execução:** 2026-09-03
- **Commit inicial:** `7072337` (Add bug_report-3.md for the prompt-rodada-3.md work — TAREFA-1 through TAREFA-7)
- **Commit final:** `9a07ee3` (Measure OCR recovery on real screenshots and stop the round — TAREFA-1, TAREFA-4)
- **Sistema operacional:** Ubuntu 24.04.4 LTS (x86_64), 62 GiB de RAM — mesma máquina das rodadas anteriores
- **Ambiente de validação:**
  - Python 3.12.3
  - `docling` 2.123.1
  - `pypdfium2` 5.13.0
  - `fastapi` 0.141.1 · `starlette` 1.6.0 · `uvicorn` 0.52.4 · `httpx2` 2.12.0
  - `setuptools` 84.0.0
  - Idêntico às rodadas anteriores — nenhuma dependência nova instalada.

**Documento real usado para a medição desta rodada:**
- `teste.pdf` (~42 MB, 1310 páginas, manual real de administração NGFW) — mesmo arquivo das rodadas 2 e 3, ainda presente só no filesystem local, não commitado. Desta faixa foram extraídas as 46 páginas (50–95) usadas na medição da TAREFA-1, contendo as duas capturas de tela citadas no prompt (páginas 61 e 91).

**Sobre `bug_report.md`, `bug_report-2.md`, `bug_report-3.md`:** o usuário apagou esses três arquivos do disco durante esta sessão. Numa interação anterior eu os havia restaurado via `git restore` sem perguntar antes — o usuário apontou o erro ("eu apaguei esses arquivos, por que você está restaurando sem me perguntar?") e eu reconheci que não deveria ter feito isso. A decisão de mantê-los apagados é do usuário; **não foram tocados nesta rodada** (continuam fora do disco, intactos no histórico do git).

---

## 2. Quadro-resumo

| ID | Prioridade | Estado | Executado | Commit | Testes adicionados |
|---|---|---|---|---|---|
| TAREFA-1 | máxima (decide o resto da rodada) | **HIPÓTESE REFUTADA** (para os dois exemplos que a motivaram) | sim — medição + registro | `9a07ee3` | 0 (nenhuma mudança de código) |
| TAREFA-2 | condicional à TAREFA-1 | **NÃO EXECUTADA** — condição de parada do próprio prompt foi atingida | — | — | — |
| TAREFA-3 | condicional à TAREFA-1 | **NÃO EXECUTADA** — mesma razão | — | — | — |
| TAREFA-4 | — | REGISTRADA, não implementada (por instrução explícita) | sim — só registro | `9a07ee3` | 0 |

**Contagens:**
- Hipótese testada e refutada (para o caso motivador): 1 (TAREFA-1)
- Não executadas por condição de parada explícita do prompt: 2 (TAREFA-2, TAREFA-3)
- Registradas sem implementação (por instrução): 1 (TAREFA-4)
- Total de testes adicionados: **0** — rodada não alterou nenhum comportamento de código, só documentação (`README.md`, `docs/backend.md`)
- Critério de conclusão da rodada (`prompt-rodada-4.md`): *"Se o OCR não recuperar o texto das capturas, pare, registre isso como achado, e não faça as TAREFAS 2 e 3"* — **atendido exatamente como escrito.**

---

## 3. Detalhe por item

### TAREFA-1 — Medir o custo real do OCR em documento nativo com imagens

- **Estado:** hipótese do prompt (OCR recupera o conteúdo das duas capturas citadas) **refutada pela medição**, com uma ressalva importante — ver "Leitura" abaixo.
- **Método:** faixa real de 46 páginas (50–95, índices 49–94) extraída de `teste.pdf` via `pypdfium2.PdfDocument.import_pages()` — inclui as duas capturas citadas no prompt (página 61: XML diff de Config Audit; página 91: tela de Logging and Reporting Settings). Script de medição (`medir_ocr.py`, scratchpad da sessão) converteu a faixa duas vezes — `ocr=False` e `ocr=True` — com `settings.debug.profile_pipeline_timings = True` ligado, e `/usr/bin/time -v` em volta de cada execução para o pico de RSS real do processo.
- **Dados coletados:**

  | Medida | `ocr=False` | `ocr=True` |
  |---|---|---|
  | Tempo total (`time.perf_counter`) | 114,27 s | 157,69 s (**1,38×**) |
  | Pico de RSS (`Maximum resident set size`, `/usr/bin/time -v`) | 18,72 GB | 44,31 GB (**2,37×**) |
  | `conv_res.timings['ocr']` | inexistente (estágio não roda) | 135,69 s — **86% do tempo total** |
  | `conv_res.timings['layout']` | 31,35 s | 30,64 s |
  | `conv_res.timings['table_structure']` | 17,23 s | 16,99 s |
  | Status | `ConversionStatus.SUCCESS` | `ConversionStatus.SUCCESS` |

  Layout e TableFormer custam praticamente o mesmo nos dois modos — confirma a leitura do prompt de que o pipeline PDF-aware não refaz esses estágios por causa do OCR; o custo adicional é quase inteiramente o próprio estágio de OCR.

- **Verificação dos termos-alvo** (os cinco citados no prompt, verificados manualmente pelo usuário como presentes dentro das capturas de tela):

  | Termo | `ocr=False` | `ocr=True` |
  |---|---|---|
  | `scp_admin` | presente | presente |
  | `password-complexity` | ausente | ausente |
  | `update-server` | ausente | ausente |
  | `Max Rows in CSV Export` | ausente | ausente |
  | `corp-syslog` | ausente | ausente |

  `scp_admin` é **falso positivo**: `grep` no `.md` sem OCR mostrou que o termo já aparece num exemplo de comando SCP legítimo, sem relação com a captura da página 61, em outro ponto da mesma faixa de 46 páginas (linha 948) — não veio do OCR, e não indica recuperação de conteúdo. Os outros quatro termos não apareceram em **nenhum** dos dois modos.

  A mesma checagem foi repetida especificamente na região da página 91 (seção "Logging and Reporting Settings", localizada via `grep -n` nos dois arquivos): o `<!-- image -->` correspondente é idêntico nos dois `.md` — nenhum texto novo apareceu antes, depois, ou no lugar dele no modo com OCR.

- **Diferença de conteúdo, olhando o documento inteiro (não só os termos-alvo):** um `diff` entre os dois `.md` de saída mostra que o OCR **funciona mecanicamente** e recupera texto real, coerente e substancial de várias outras capturas de tela na mesma faixa de 46 páginas — por exemplo, o texto de um diálogo de busca ("Global Find... Search results appear here..."), árvores inteiras de checkbox de permissão ("PDF Summary Reports", "User Activity Report", "Application Statistics", "Threat Log", etc.) e um parágrafo longo e coerente de um exemplo de perfil de administrador (Admin Role Profile). Isso descarta a hipótese alternativa de que o OCR simplesmente não roda ou falha por completo — ele processa a maioria das 65 imagens da faixa e produz texto útil para várias delas.

  Mas, especificamente nas duas capturas citadas no prompt, o resultado foi diferente:
  - **Página 61** (XML diff de Config Audit): nenhum termo técnico recuperável — só fragmentos ilegíveis próximos às imagens da seção ("Lon Sottinae" depois do par de imagens do STEP 3; "Version 1" / "Version 6" depois do par do STEP 5/6). Não é o conteúdo esperado, é ruído de OCR.
  - **Página 91** (Logging and Reporting Settings): nada — o placeholder `<!-- image -->` não ganhou nenhum texto ao redor em nenhum dos dois modos.

  O log de execução do modo `ocr=True` registrou exatamente duas ocorrências de `RapidOCR returned empty result!` — consistente com falha silenciosa do OCR em pelo menos parte dessas capturas específicas (a imagem da seção de Config Audit tem múltiplas figuras; pelo menos uma delas retornou vazio).

- **Leitura (a nuance que decide o resultado da rodada):** o sinal não é "OCR nunca ajuda". É "OCR ajuda em capturas simples de UI (diálogos, menus, árvores de checkbox) e falha nas capturas densas de texto técnico monoespaçado/tabular (diff de XML, tela de configuração com grade de campos e valores)" — que são justamente o tipo de captura mais valiosa de recuperar, e são exatamente os dois exemplos que motivaram a rodada inteira. RapidOCR (modelo leve, PP-OCRv6) parece não lidar bem com texto pequeno e denso em fontes monoespaçadas ou grades tabulares dentro de uma imagem, mesmo lidando razoavelmente bem com texto de UI padrão (fontes maiores, mais espaçadas, fundo mais limpo).
- **Custo adicional que reforça a decisão de não prosseguir:** o pico de 44,3 GB de RSS numa faixa de só 46 páginas é da mesma ordem de grandeza do pico de ~44,2 GB já observado (rodada 3) convertendo o `teste.pdf` **inteiro** (1310 páginas) **sem** OCR. Isso indica que ligar OCR amplamente — como a TAREFA-2 proporia — multiplicaria o custo de memória por página processada por OCR de forma severa, e faria isso num documento que já opera perto do limite de segurança sem OCR nenhum. Ligar essa opção antes de a rodada de gestão de memória (fora do escopo desta) existir seria arriscar OOM em qualquer documento grande com muitas capturas.
- **Decisão, seguindo a condição de parada explícita do próprio prompt** (*"Se o OCR não recuperar o texto das capturas, pare, registre isso como achado, e não faça as TAREFAS 2 e 3"*): para os dois exemplos verificados e citados como motivação, o OCR **não** recuperou o conteúdo. A condição de parada foi atingida. TAREFA-2 e TAREFA-3 não foram executadas. `tem_camada_de_texto()` (rodada 3, TAREFA-3) **não foi alterada** — permanece a decisão apropriada, já que a mudança proposta não entregaria o benefício que a motivou, ao custo medido de memória e tempo.
- **Correção aplicada:** nenhuma mudança de comportamento de código. Registro da medição completa (tabelas, achados, leitura, decisão) adicionado como nova seção em `docs/backend.md` ("Medição: OCR recupera capturas de tela em documento nativo?") e um resumo mais curto em `README.md` (seção "OCR automático por documento").
- **Testes de regressão:** nenhum necessário — nenhum comportamento de código mudou. As duas suítes rodaram limpas antes e depois (ver seção 4).
- **Risco residual:** a medição usou uma única faixa de 46 páginas de um único documento. Não é uma prova de que OCR nunca recupera conteúdo denso em nenhum documento — é evidência suficiente, para o caso concreto que motivou esta rodada, de que a mudança proposta não entregaria o ganho esperado ao custo medido. Se um documento diferente aparecer com capturas técnicas que o RapidOCR consiga ler bem, essa decisão deveria ser revisitada com dados novos — não é definitiva para sempre, é definitiva para a evidência disponível hoje.

### TAREFA-2 — Segundo sinal na detecção: área de imagem

- **Estado:** **NÃO EXECUTADA.** O próprio prompt condiciona esta tarefa a *"Só execute se a TAREFA-1 confirmar que o OCR recupera o conteúdo"* — a TAREFA-1 não confirmou isso para os dois exemplos motivadores (ver acima). Nenhum código foi escrito, nenhum limiar de 120.000 pixels ou 10% de páginas foi implementado.

### TAREFA-3 — Expor por que o OCR foi ligado

- **Estado:** **NÃO EXECUTADA**, pela mesma razão da TAREFA-2 — depende diretamente dela. `ocr_origem` continua distinguindo só `"detectado"` de `"forcado"`, como na rodada 3. Nenhuma mudança em `README.md` ou `docs/backend.md` além da correção de premissa registrada na TAREFA-1 (que é textual/de registro, não a expansão de campo que esta tarefa pediria).

### TAREFA-4 — Decisão registrada sobre `<!-- image -->`

- **Estado:** avaliado e registrado, **não implementado**, exatamente como o prompt pediu ("Não implemente. Avalie e registre.") — independente do resultado da TAREFA-1, já que é uma questão separada do OCR.
- **Avaliação registrada** (texto completo em `docs/backend.md`, seção "Decisão registrada (não implementada): embutir imagens no Markdown"):
  - **O que seria preciso:** `generate_picture_images=True` nas `PdfPipelineOptions` do Docling faz o bitmap recortado de cada figura ficar anexado ao `PictureItem`; o modo de imagem do `export_to_markdown()` (`ImageRefMode.EMBEDDED` vs `REFERENCED`) decide entre `data:` URI inline no `.md` ou arquivo separado ao lado dele. Nenhuma das duas opções está configurada hoje.
  - **Custo:** mantém bitmaps de página em memória durante toda a conversão, somando-se diretamente ao pico de RSS já observado (44 GB no `teste.pdf` inteiro, sem essa opção, e sem OCR — ver rodada 3 TAREFA-4). `teste.pdf` tem 1357 imagens no total; ligar isso sem a rodada de gestão de memória (futura, fora de escopo) arriscaria OOM em qualquer documento grande e rico em imagens.
  - **Escopo:** avaliado como fazendo mais sentido por job (mesmo padrão do seletor de OCR `modo_ocr`) do que como decisão global — um usuário que precisa das imagens paga o custo deliberadamente, sem impor isso a todos os outros jobs da fila.
  - Este item foi registrado como insumo explícito para a rodada de gestão de memória, sem antecipação de implementação, por instrução direta do prompt.
- **Testes de regressão:** não aplicável (nenhuma mudança de comportamento).

---

## 4. Estado das suítes antes/depois

Nenhum código de produção foi alterado nesta rodada (só `README.md` e `docs/backend.md`). Ainda assim, as duas suítes foram executadas antes de começar e depois do commit final, seguindo o protocolo das rodadas anteriores.

**Antes** (commit `7072337`, estado final da rodada 3):
```
test_pdf_to_md.py ......................................... 99 passed, 6 subtests passed
backend/tests/    ......................................... 94 passed, 15 subtests passed
```

**Depois** (commit `9a07ee3`, estado final desta rodada):
```
test_pdf_to_md.py ......................................... 99 passed, 6 subtests passed
backend/tests/    ......................................... 94 passed, 15 subtests passed
```

Idêntico nos dois pontos — esperado, já que nenhum código foi tocado. Critério de conclusão da rodada ("as duas suítes terminam limpas") **atendido**.

---

## 5. Pendências

- **`bug_report.md`, `bug_report-2.md`, `bug_report-3.md` apagados pelo usuário** — permanecem fora do disco por decisão do usuário; não foram restaurados nesta rodada. Continuam recuperáveis via `git show <commit>:<arquivo>` a qualquer momento, caso o usuário queira revisitá-los.
- **Push:** os commits das rodadas 3 e 4 (a partir de `b540241` até `9a07ee3`) ainda não foram confirmados como enviados a `origin/main` nesta sessão — branch está à frente do remoto. Nenhuma ação de push foi pedida ainda para este trabalho.
- **`teste.pdf` e `prompt-rodada-4.md`** continuam não rastreados no repositório (mesmo tratamento das rodadas anteriores: arquivo de teste grande e prompt já consumido, respectivamente) — não apagados, por não ter sido pedido.
- **TAREFA-2 e TAREFA-3 ficam disponíveis para retomar no futuro**, caso surja evidência nova (outro documento, ou uma configuração diferente do RapidOCR) que mude a conclusão da TAREFA-1. A decisão desta rodada é baseada na evidência disponível hoje, não uma proibição permanente.
- **Rodada de gestão de memória** (próxima, fora do escopo desta): tem agora dois insumos diretos desta rodada — o registro da TAREFA-4 sobre embutir imagens, e o dado de que OCR sozinho já multiplica o pico de RSS por 2,4× numa faixa pequena, o que deve pesar em qualquer decisão futura de tornar OCR mais agressivo por padrão.
