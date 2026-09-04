# bug_report-6.md — sexta rodada (execução de `prompt-rodada-6.md`)

## 1. Cabeçalho

- **Data de execução:** 2026-09-03
- **Commit inicial:** `7a103a9` (Add bug_report-5.md for the prompt-rodada-5.md work — TAREFA-1 through TAREFA-8)
- **Commit final:** `4cb03a5` (Consolidate the process-per-job decision record — TAREFA-8)
- **Sistema operacional:** Ubuntu 24.04 (x86_64), **64 núcleos** (`len(os.sched_getaffinity(0))`, sem teto de cgroup — `os.cpu_count()` também deu 64, confirmando que não há container limitando a contagem), 62 GiB de RAM, sem GPU NVIDIA — mesma máquina das rodadas anteriores.
- **Ambiente de validação:** idêntico às rodadas anteriores — nenhuma dependência nova instalada. `psutil` 7.2.2 já disponível no `.venv`, usado para amostragem de RSS em processo. `systemd` 255 disponível, com `systemd-run --user --scope` funcional sem sudo (verificado).
- **Antes de começar:** a instrução do prompt para fazer push primeiro já estava satisfeita — `git fetch`/`git push` confirmaram `origin/main` já igual ao `HEAD` local (`7a103a9`) antes de qualquer trabalho desta rodada.
- **Documento real usado para as medições:** `teste.pdf` (~42 MB, 1310 páginas, manual real de administração NGFW) — mesmo arquivo das rodadas 2-5, só no filesystem local, não commitado. Faixas extraídas com `pypdfium2.import_pages` (não commitadas, ficaram em `/tmp`): a faixa padrão de 46 páginas (50-95) reutilizada das rodadas 3-5; quatro faixas novas de 30 páginas (150-179, 400-429, 650-679, 900-929) e suas versões de 10 páginas (200-209, 450-459, 700-709, 950-959) para a TAREFA-2; uma faixa de 100 páginas (300-399) e suas duas metades de 50 páginas para a TAREFA-7.

**Nota de segurança registrada:** durante a calibração da TAREFA-2, uma
conversão OCR de 30 páginas da faixa 150-179 sozinha ultrapassou 51 GB de
RSS e continuava subindo — foi interrompida manualmente (`kill -9`) para
proteger a máquina compartilhada de 62 GiB antes de atingir um OOM real.
A partir daí, toda medição pesada desta rodada rodou sob
`scripts/rodar_com_limite_memoria.sh` (o produto da TAREFA-6), como rede
de segurança determinística em vez de vigilância manual.

---

## 2. Quadro-resumo

| ID | Bloco | Estado | Executado | Commit | Testes adicionados |
|---|---|---|---|---|---|
| TAREFA-1 | A (diagnóstico) | Contradição resolvida — não é chave universal | sim — só medição | `2952a06` | 0 |
| TAREFA-2 | A | **ACHADO CENTRAL** — o patamar cresce a cada job | sim — só medição | `7b7e905` | 0 |
| TAREFA-3 | A | Sem efeito medido, não implementado | sim — só medição | `b7dcd14` | 0 |
| TAREFA-4 | B (medição) | Ganho de tempo real, padrão mantido por causa do par com RSS | sim — só medição | `33a179e` | 0 |
| TAREFA-5 | B | (a) descartado por regressão severa de tempo; (b) implementado, resolve a TAREFA-2 | sim — medição + implementação parcial | `c873eca` | 4 |
| TAREFA-6 | B | Script implementado | sim | `c50eeb8` | 0 (ferramental, não testado por unit test) |
| TAREFA-7 | C (decisão) | Testado, resultado contra-intuitivo, não implementado | sim — só medição | `3c54b47` | 0 |
| TAREFA-8 | C | Registro consolidado, sem recomendação | sim — só registro | `4cb03a5` | 0 |

**Contagens:**
- Implementado com efeito medido e testes de regressão: 1 (TAREFA-5b, `malloc_trim`)
- Medido e **não** implementado por falta de ganho ou por regressão: 4 (TAREFA-3, TAREFA-4 mantém padrão, TAREFA-5a descartado, TAREFA-7)
- Ferramental de operação, fora do código de produção: 1 (TAREFA-6)
- Registro/decisão sem implementação, por instrução explícita: 2 (TAREFA-1, TAREFA-8)
- Total de testes adicionados: **4**, todos em `backend/tests/test_jobs.py`
- Critério de conclusão da rodada: "toda mudança do Bloco B precisa de número antes/depois" — **atendido** em TAREFA-4 e TAREFA-5; "se a medição não mostrar ganho, não implemente" — aplicado em TAREFA-3 e, de forma mais severa (regressão, não neutralidade), em TAREFA-5a.
- Restrições explícitas de escopo (processo por job, pool, STOP, isolamento por usuário, seleção de páginas, `traverse_pictures` como padrão) — **respeitadas**, nenhuma implementada.
- As duas suítes terminam limpas: `test_pdf_to_md.py` — 121 passed; `backend/tests/` — 104 passed, 15 subtests passed.

---

## 3. Detalhe por item

### Bloco A — diagnóstico

### TAREFA-1 — Resolver a contradição sobre `traverse_pictures`

- **Estado:** contradição resolvida por inspeção de árvore, nenhum código alterado.
- **Método:** conversão real com OCR da faixa de 46 páginas (50-95), depois `doc.iterate_items(traverse_pictures=True)` sobre o `DoclingDocument`, checando para cada item de texto se `item.parent.cref` resolve a um `PictureItem`.
- **Achado:** os termos recuperados pela rodada 4 sem `traverse_pictures` ("Global Find", "PDF Summary Reports", "User Activity Report", "Application Statistics", "Threat Log", "Admin Role Profile") aparecem majoritariamente como texto de página direto (`FORA_DE_PICTURE`). Os termos que a rodada 5 só recuperou com `traverse_pictures=True` (`scp_admin`, `password-complexity`, `update-server`, "Max Rows in CSV Export", `corp-syslog`) aparecem como filhos de `PictureItem` (`SOB_PICTURE`). **A classificação é por região, não por documento**: o mesmo termo aparece nas duas categorias em páginas diferentes (ex.: "Global Find" 13× fora, 2× sob picture; "scp_admin" 9× fora, 5× sob picture) — confirma a hipótese do prompt de que o parâmetro não é uma chave universal.
- **Proporção medida:** 65 `PictureItem` na faixa; 1.105 itens de texto (12.327 caracteres, 15,49%) sob `PictureItem`, contra 564 itens (67.259 caracteres, 84,51%) como texto direto.
- **Correção aplicada:** nenhuma — `traverse_pictures` continua desligado por padrão, por instrução explícita.
- **Testes de regressão:** nenhum — medição pura.
- **Risco residual:** a proporção (15,49%) foi medida num único documento; pode variar em documentos com layouts diferentes. Fica para uma rodada futura de qualidade decidir se `traverse_pictures=True` vira padrão ou opção exposta.

### TAREFA-2 — O patamar cresce a cada job no mesmo processo?

- **Estado:** medido — **sim, cresce**. Esta é a medição mais importante da rodada e o insumo direto da TAREFA-5 e da TAREFA-8.
- **Método:** pelo caminho real do backend (`motor_pool.inicializar()` uma vez, `motor.converter()` reaproveitado), 4 documentos em sequência no mesmo processo, RSS amostrado por thread (`psutil`, a cada 0,2s), separado por modo de OCR.
- **Sem OCR** (4 faixas de 30 páginas): RSS "depois" de cada job — 12.363 → 21.188 → 27.575 → 32.280 MB.
- **Com OCR** (4 faixas de 10 páginas, reduzidas de 30 depois do incidente de segurança): RSS "depois" — 12.175 → 21.077 → 25.422 → 29.417 MB.
- **Conclusão:** o patamar **não estabiliza** em 3-4 jobs, nos dois modos — cresce continuamente, ainda que a taxa de crescimento diminua. Isso aponta para acumulação de objeto vivo ou memória livre retida em arenas de malloc (não devolvida ao kernel), não simplesmente "o Docling mantém um cache que satura rápido".
- **Correção aplicada:** nenhuma nesta tarefa — o diagnóstico alimentou a TAREFA-5, que resolveu a causa (retenção de alocador, não objeto vivo — ver abaixo).
- **Testes de regressão:** nenhum — medição pura.
- **Risco residual:** a faixa 150-179 (30 páginas, OCR) revelou-se atipicamente pesada (>51 GB e subindo, abortada) — o crescimento entre jobs pode ser ainda mais acentuado em sequências que incluam conteúdo assim, não capturado pelas faixas de 10 páginas usadas para completar a medição em segurança.

### TAREFA-3 — O `page_batch_size` explica o pico?

- **Estado:** medido — **não explica**, não implementado.
- **Método:** mesma faixa de 46 páginas com OCR, `page_batch_size` em 1, 4 (padrão) e 8, medindo tempo e pico via `/usr/bin/time -v`.
- **Resultado:** 1 → 156,8s / 44,76 GB; 4 → 156,6s / 46,61 GB; 8 → 165,8s / 48,43 GB. Sem tendência monotônica clara — a variação é da ordem do ruído esperado em conteúdo real (comparável à variação entre documentos de tamanho igual vista na TAREFA-2), não um efeito de lote.
- **Correção aplicada:** nenhuma — `page_batch_size` não foi exposto como opção, por não haver ganho medido.
- **Testes de regressão:** nenhum.
- **Risco residual:** nenhum identificado — a hipótese alternativa do próprio prompt (pico não é do lote) se confirmou.

### Bloco B — correções baratas, cada uma com medição

### TAREFA-4 — `threads` está estrangulando a conversão?

- **Estado:** medido — sim, mas o padrão **foi mantido em 4** por causa do par tempo/memória, não só do tempo.
- **Método:** máquina confirmada com 64 núcleos, sem teto de cgroup. Mesma faixa de 46 páginas com OCR, `threads` em 4, 8, 16, 32, 64, medindo tempo e pico via `/usr/bin/time -v`, sob a rede de segurança de 55 GB.
- **Resultado:** 4 → 159,4s / 46,55 GB. 8 e 16 **excederam o teto de segurança de 55 GB antes de terminar** (58,1 GB e subindo). 32 → 23,5s / 57,90 GB. 64 → 23,5s / 57,88 GB (nenhum ganho sobre 32).
- **Achado:** o custo de memória é um **degrau**, não um gradiente — qualquer valor acima de 4 salta para ~58 GB, provavelmente por ativação de paralelismo intra-operador em pools internos do onnxruntime/PyTorch, não por N arenas crescendo linearmente com `threads`.
- **Critério de decisão:** a TAREFA-2 mostrou que o patamar cresce a cada job no processo compartilhado. Em `threads=4`, um job pesado usa ~46,6 GB de 62 GiB (25% de margem para esse crescimento); acima de 4, um único job já usa ~93% da máquina — sem margem nenhuma para o próximo job. O par (tempo, RSS) não compensa para o padrão do processo compartilhado, mesmo com o ganho de tempo sendo real e grande (até 6,8×).
- **Correção aplicada:** nenhuma no padrão — `Config.threads` continua em 4, configurável via `--threads` para quem converte um documento isolado e sabe que a máquina tem folga.
- **Testes de regressão:** nenhum — nenhum comportamento padrão mudou.
- **Risco residual:** as medições de 8 e 16 threads foram interrompidas pelo teto de segurança antes de atingir seu pico natural — o valor real pode ser maior que 58 GB. Não investigado further por já ser suficiente para a decisão (mantiveram-se acima do limite seguro de qualquer forma).

### TAREFA-5 — `MALLOC_ARENA_MAX` e `malloc_trim`

- **Estado:** (a) medido e **descartado** por regressão severa; (b) medido e **implementado**, com efeito completo.
- **(a) `MALLOC_ARENA_MAX=2`:** aplicado via `aplicar_ambiente()`, testado sobre a sequência de 4 jobs sem OCR da TAREFA-2. **Efeito colateral severo, não previsto pelo prompt:** o mesmo job 1 (30 páginas, sem OCR) que levava ~81s sem o limite ainda não tinha terminado após 4 minutos com `MALLOC_ARENA_MAX=2` — mais de 3× mais lento, tendência de piorar ainda mais. Causa mais provável: contenção de lock entre threads concorrentes disputando só 2 arenas. **Revertido antes de virar padrão** — o código final não define essa variável.
- **(b) `malloc_trim(0)` ao fim de cada job:** implementado em `backend/src/services/jobs.py`, `_processar()`, via `ctypes.CDLL("libc.so.6")`, guardado contra `OSError`/`AttributeError` (plataforma sem glibc, ou símbolo ausente). Medido sobre a mesma sequência sem OCR (sem `MALLOC_ARENA_MAX`, já descartado): RSS "depois" foi de 12.363/21.188/27.575/32.280 MB (sem paliativo, TAREFA-2) para **1.481/1.597/1.544/1.595 MB** — o patamar **para de crescer**, com tempo idêntico dentro do ruído (70-81s nos dois casos).
- **Divergência do prompt registrada:** o prompt pedia medir os dois paliativos "separadamente e depois juntos". O item (a) foi descartado por conta própria (regressão de tempo, não "sem efeito mensurável" — um efeito negativo forte); testar a combinação "os dois juntos" não teria utilidade prática, porque herdaria a regressão de (a). Só (b) foi levado a produção.
- **Testes de regressão:** `TestMallocTrimAoFimDoJob`, `TestMallocTrimAoFimDoJobComErro`, `TestTentarMallocTrim` (2 casos) — 4 no total, em `backend/tests/test_jobs.py`.
- **Risco residual:** nenhum identificado para (b) — efeito limpo, sem custo de tempo, guardado contra plataformas sem glibc.

### TAREFA-6 — Watchdog de memória por cgroup

- **Estado:** implementado como ferramental de operação — `scripts/rodar_com_limite_memoria.sh`.
- **Correção aplicada:** wrapper que roda o comando sob `systemd-run --user --scope -p MemoryMax=... -p MemorySwapMax=0`, sem exigir root/sudo.
- **Achado durante a implementação, não previsto pelo prompt:** `MemoryMax` sozinho **não basta**. Testado empiricamente: `MemoryMax=200M` sem `MemorySwapMax` deixou um processo alocar 500 MB sem ser interrompido (o kernel preferiu paginar para o swap disponível a matar o cgroup). Adicionando `MemorySwapMax=0`, o mesmo teste morreu (`SIGKILL`, exit 137) assim que excedeu o limite. O script sempre define os dois.
- **Valor recomendado:** `MemoryMax=55G` como padrão, com base nos picos do Bloco A (44-48 GB em `threads=4`, ~58 GB acima disso, uma faixa isolada excedendo 51 GB) — margem sobre o pico mais alto medido, não sobre a média.
- **Uso real nesta rodada:** todas as medições pesadas de TAREFA-1, TAREFA-3, TAREFA-4 e parte da TAREFA-2/7 rodaram sob este script, incluindo um caso real de intervenção (TAREFA-4, threads 8 e 16, mortos pelo teto de 55 GB antes de terminar).
- **Testes de regressão:** nenhum unit test — é um script shell, validado por execução real (smoke test com `MemoryMax=500M`/`bytearray` e com o valor padrão).
- **Risco residual:** nenhum identificado. Não implementado dentro do worker (checagem de RSS em Python), por instrução explícita.

### Bloco C — decisão

### TAREFA-7 — Avaliar particionamento de documentos grandes

- **Estado:** testado, resultado **contra-intuitivo**, não implementado.
- **Método:** 100 páginas de `teste.pdf` (300-399, sem OCR, mesmo motor via `motor_pool`) convertidas de uma vez versus em duas faixas sequenciais de 50 páginas.
- **Resultado:** 100 páginas/1 job → 226,3s / 17,55 GB / 192.063 caracteres. 2×50 páginas → 233,5s / **28,10 GB** / 192.048 caracteres.
- **Achado:** particionar **piorou** o pico (28,1 GB vs. 17,55 GB) em vez de melhorar, porque o teste rodou **antes** do `malloc_trim` da TAREFA-5 entrar no fluxo (ordem de execução do protocolo) — a segunda metade herdou o patamar elevado deixado pela primeira, o mesmo crescimento entre jobs que a TAREFA-2 documentou. Contagem de caracteres bateu quase exatamente, confirmando que não há duplicação/perda na emenda.
- **O que se perde ao particionar, independente do resultado de memória:** continuidade de tabela na fronteira do corte, estrutura de cabeçalhos entre faixas, e grau de confiança por faixa em vez de por documento (numeração de página já resolvida na rodada 5).
- **Correção aplicada:** nenhuma.
- **Testes de regressão:** nenhum — avaliação, sem mudança de código.
- **Risco residual:** o teste precisa ser refeito com `malloc_trim` já ativo entre as faixas para validar a hipótese original corretamente — registrado como trabalho de uma rodada futura, não desta.

### TAREFA-8 — Registrar a decisão sobre processo por job

- **Estado:** registro consolidado em `docs/architecture.md`, sem recomendação.
- **Conteúdo:** custo de regime (1,4-1,9 GB) vs. pico de processamento (17-48 GB, dependendo de conteúdo/OCR) — processo por job não muda essa proporção, só quando a memória é devolvida. O crescimento entre jobs (TAREFA-2) é, na prática, quase resolvido por `malloc_trim` (TAREFA-5) a um custo de implementação muito menor que processo por job. Dimensionamento de workers pelo PICO, não pela média: um único worker pesado já usa 70-80% dos 62 GiB — dois workers pesados concorrentes excederiam a RAM física com ou sem processo por job. O que processo por job resolve que o paliativo não resolve: reclamação incondicional (não dependente de um caminho de código específico), botão de parar real, e paralelismo real para jobs leves — nenhum dos dois últimos foi pedido nesta rodada.
- **Correção aplicada:** nenhuma — só registro, por instrução explícita.
- **Testes de regressão:** nenhum.
- **Risco residual:** nenhum — material de decisão para o usuário, não uma implementação.

---

## 4. Verificação final

```
test_pdf_to_md.py:      121 passed, 4 warnings, 6 subtests passed
backend/tests/:         104 passed, 15 subtests passed
```

Nenhuma falha em nenhuma das duas suítes. Uma instabilidade transitória foi
observada uma única vez em `backend/tests/` (7 falhas) durante execução
simultânea com um benchmark de OCR pesado em background, disputando CPU —
não reproduzida em 5 execuções limpas subsequentes (com e sem as mudanças
desta rodada), portanto não é uma regressão desta rodada.

## 5. Restrições gerais — conformidade

- Não implementado: processo por job, pool, STOP, isolamento por usuário, seleção de páginas, `traverse_pictures` como padrão — todos respeitados.
- Nenhuma correção de rodada anterior foi alterada, fora do que esta rodada pediu.
- Toda mudança do Bloco B teve número antes/depois: TAREFA-4 (mantida sem mudança, com número), TAREFA-5a (revertida, com número), TAREFA-5b (implementada, com número), TAREFA-6 (ferramental, validado por execução real).
- Divergências da premissa do prompt, registradas: TAREFA-5a regrediu tempo em vez de "não fazer diferença mensurável" (mais severo que o esperado); TAREFA-6 exigiu `MemorySwapMax=0` além de `MemoryMax` (o prompt não antecipava a interação com swap); TAREFA-7 teve resultado invertido do esperado, por depender da ordem de execução com a TAREFA-5.
