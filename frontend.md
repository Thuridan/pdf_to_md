# Frontend — design da UI web

Design detalhado de `frontend/`: a interface de upload/fila/download servida
estaticamente pelo backend. Para as rotas que ela consome, ver
[`backend.md`](backend.md); para a arquitetura geral, ver
[`architecture.md`](architecture.md).

## Decisão fundamental: sem build step

```
frontend/
├── index.html
├── app.css
└── app.js
```

HTML/CSS/JS puro, sem framework, sem bundler, sem `node_modules`. Isso não é
uma limitação temporária — é uma escolha de design deliberada para este
projeto:

- O backend serve `frontend/` via `StaticFiles(directory=..., html=True)`
  ([`app.py`](backend.md#apppy--bootstrap-da-aplicação)) — três arquivos
  estáticos não precisam de pipeline de build, só precisam existir no disco.
- A aplicação inteira é uma tela (upload → fila → download); a complexidade
  que um framework de componentes resolveria (roteamento, state management
  entre telas, composição profunda) não existe aqui.
- Reduz a superfície de dependências do projeto inteiro a zero pacotes npm —
  coerente com o resto do repositório, que já evita dependências
  desnecessárias (ver [`dependencies.md`](dependencies.md)).

O único recurso externo é uma folha de estilo do Google Fonts (Sora, Work
Sans, IBM Plex Mono) carregada via `<link>` no `<head>` — sem hospedar fontes
localmente, mas também sem JS de terceiros.

## `index.html` — estrutura semântica

```
.pagina
├── header.cabecalho          → wordmark, pill do motor ativo, toggle de tema
├── #dropzone                 → área de upload (drag&drop + click + teclado)
├── section.fila-card
│   ├── .fila-cabecalho       → resumo da fila + .fila-acoes (limpar / baixar tudo)
│   └── #jobs-list            → lista de jobs (renderizada via JS)
└── .rodape-nota
```

Cada elemento interativo tem um `id` estável que `app.js` referencia
diretamente (sem framework de data-binding) — `dropzone`, `file-input`,
`jobs-list`, `queue-summary`, `download-all`, `clear-done`, `motor-pill`,
`theme-toggle`. O `#dropzone` é `tabindex="0" role="button"` com um
`aria-label` descritivo, e responde a `Enter`/`Espaço` além de clique —
acessível por teclado sem JS extra além do listener de `keydown`.

## `app.css` — sistema de tema

### Tokens de cor via `oklch()`

```css
:root {
  --bg: oklch(98% 0.004 250);
  --surface: oklch(100% 0 0);
  --text: oklch(21% 0.01 250);
  --accent: oklch(56% 0.19 285);
  --blue: ...; --amber: ...; --green: ...; --red: ...;   /* + suas variantes -bg */
}
html[data-theme="dark"] {
  --bg: oklch(18% 0.012 260);
  /* mesmos nomes de token, valores recalibrados para fundo escuro */
}
```

Todas as cores da UI são consumidas via `var(--token)`, nunca hard-coded nos
seletores — trocar de tema é só trocar o atributo `data-theme` na raiz do
documento; nenhum seletor de componente precisa saber que o tema mudou.
`oklch()` foi escolhido (em vez de `hsl()`/hex) porque permite ajustar
luminosidade perceptual de forma previsível entre os dois temas mantendo o
mesmo matiz — é por isso que `--accent` no dark mode é bem mais claro
(`76%` vs `56%` de luminosidade) sem mudar de matiz (`285`).

### Persistência e detecção do tema

```js
const CHAVE_TEMA = "pdf-to-md:tema";
(function iniciarTema() {
  const salvo = localStorage.getItem(CHAVE_TEMA);
  const preferido = salvo || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  aplicarTema(preferido);
})();
```

Ordem de precedência: preferência salva pelo usuário > preferência do SO >
claro (padrão). `aplicarTema()` seta `documentElement.dataset.theme` (o que
ativa o bloco `html[data-theme="dark"]` do CSS) e persiste a escolha em
`localStorage`, então uma escolha manual do usuário sobrevive a reloads e
não é sobrescrita pela preferência do SO na próxima visita.

### Componentes de estado visual

- **Badges** (`badge-amber/blue/green/red`) — um por status de job
  (`na_fila`/`processando`/`concluido`/`erro`), cor e ícone fixos por
  status, não combinados dinamicamente.
- **Barra de progresso indeterminada** — `.progresso-indeterminado` usa um
  gradiente listrado animado (`background-position` via `@keyframes
  listrar`) para o caso em que `paginas_totais` é `null` (PDF sem
  pré-checagem de páginas) e não há percentual real para mostrar.
- **`.acao-slot`** é um `flex` container (não largura fixa) porque uma linha
  de job concluído mostra dois botões lado a lado (baixar + remover),
  enquanto uma na fila/com erro mostra só um (remover).

## `app.js` — modelo de estado e renderização

### Estado: um array, um poll, um re-render completo

```js
let jobsCache = [];

async function atualizarFila() {
  const dados = await fetch("/api/jobs").then(r => r.json());
  jobsCache = dados.jobs || [];
  renderizarFila();
}

carregarMotor();
atualizarFila();
setInterval(atualizarFila, POLL_MS);   // POLL_MS = 1500
```

Não há diffing de DOM nem virtual DOM: `renderizarFila()` reconstrói
`#jobs-list.innerHTML` inteiro a cada poll, a partir do array `jobsCache`
mais recente. Essa simplicidade é intencional e proporcional ao volume de
dados (dezenas de jobs no pior caso local, não milhares) — o custo de
re-renderizar tudo é desprezível comparado à complexidade de reconciliar um
DOM parcial à mão sem framework. Polling (em vez de WebSocket/SSE) foi
escolhido pelo mesmo motivo: um endpoint HTTP simples é suficiente para o
volume e a cadência (1.5s) necessários, sem o custo de manter uma conexão
persistente e seu ciclo de vida (reconexão, backoff) no cliente.

### Renderização em camadas de função pura

```
linhaJob(job, totalNaFila)
├── metaLinha(job)        → "12 páginas · 2.4 MB"
└── blocoEstado(job, totalNaFila)
    ├── na_fila       → badge âmbar + "posição X de Y na fila"
    ├── processando   → badge azul + spinner + barra (%, ou indeterminada) + "página ~N/M"
    ├── concluido     → badge verde
    └── erro          → badge vermelho + mensagem (com title=mensagem completa)
```

Cada função recebe dados e devolve uma string HTML — sem efeitos colaterais,
sem acesso a `document` fora de `renderizarFila`. Isso mantém a lógica de
"o que mostrar para cada status" testável mentalmente sem precisar montar o
DOM.

`escapeHtml()` é aplicado a todo texto vindo do servidor que é interpolado
em template strings (`nome_original`, `mensagem_erro`) — a UI trata nomes de
arquivo e mensagens de erro como dados não confiáveis (um usuário pode
enviar um PDF chamado `<img src=x onerror=...>.pdf`), evitando XSS refletido
via nome de arquivo.

### Upload

```js
async function enviarArquivos(files) {
  const formData = new FormData();
  for (const arquivo of files) formData.append("files", arquivo);
  const resp = await fetch("/api/jobs", { method: "POST", body: formData });
  const corpo = await resp.json();
  if (corpo.rejeitados?.length) window.alert(`Ignorado (não é PDF): ${nomes}`);
  await atualizarFila();
}
```

Disparado por três caminhos (drop, clique + `<input type=file>`, Enter/Espaço
no dropzone) que convergem na mesma função — um único ponto de validação de
resposta (`rejeitados`) e um único `atualizarFila()` final, para a fila
refletir o upload imediatamente em vez de esperar o próximo tick do poll.

### Remoção de jobs

```js
jobsList.addEventListener("click", (e) => {
  const botao = e.target.closest(".acao-remover");
  if (botao) removerJob(botao.dataset.jobId);
});
```

Delegação de evento no container (`#jobs-list`), não um listener por botão —
necessário porque a lista inteira é recriada a cada poll (`innerHTML =`), o
que destruiria listeners anexados diretamente aos botões antigos. O mesmo
padrão vale implicitamente para os links de download, que não precisam de
JS (são `<a href>` normais). `clearDoneBtn` (botão fixo, fora da lista
recriada) usa listener direto porque ele próprio nunca é recriado —
só seu atributo `disabled` é alternado em `renderizarFila()`, conforme
`contagens.concluido + contagens.erro`.

### Download em lote

```html
<a id="download-all" class="botao-baixar-tudo" href="/api/download-zip" disabled>
```

É um link `<a>` normal apontando para `GET /api/download-zip`, não uma
chamada `fetch` + `Blob` + `URL.createObjectURL`. O navegador já sabe baixar
uma resposta `Content-Disposition: attachment` sem JS adicional; o único
papel do JS é alternar o atributo `disabled` (e bloquear o clique enquanto
ele está presente) conforme existem ou não jobs concluídos.

### Ícones

Todos os ícones são SVG inline como constantes de string
(`ICONE_PDF`, `ICONE_RELOGIO`, `ICONE_CHECK`, `ICONE_ALERTA`,
`ICONE_DOWNLOAD`, `ICONE_LIXEIRA`) interpolados diretamente no HTML gerado —
sem sprite sheet, sem ícone-fonte, sem biblioteca de ícones. `stroke:
currentColor` em todos eles significa que a cor segue o token CSS do
elemento pai (`.badge-red` colore o ícone de erro de vermelho automaticamente,
por exemplo), sem precisar de uma variante por cor.
