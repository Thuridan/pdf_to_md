"use strict";

const POLL_MS = 1500;
const CHAVE_TEMA = "pdf-to-md:tema";

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const jobsList = document.getElementById("jobs-list");
const summaryEl = document.getElementById("queue-summary");
const downloadAllBtn = document.getElementById("download-all");
const clearDoneBtn = document.getElementById("clear-done");
const motorPill = document.getElementById("motor-pill");
const themeToggle = document.getElementById("theme-toggle");
const appVersionEl = document.getElementById("app-version");
const conexaoAlertaEl = document.getElementById("conexao-alerta");
const estimativaAlertaEl = document.getElementById("estimativa-alerta");

let jobsCache = [];
// Limiar (minutos) acima do qual o banner de estimativa aparece - vem de
// /api/jobs (AVISO_ESTIMATIVA_MINUTOS no servidor); 30 e so o palpite ate a
// primeira resposta chegar.
let avisoEstimativaMinutos = 30;

// --- tema -------------------------------------------------------------
function aplicarTema(tema) {
  document.documentElement.dataset.theme = tema;
  localStorage.setItem(CHAVE_TEMA, tema);
  themeToggle.setAttribute("aria-pressed", String(tema === "dark"));
}

(function iniciarTema() {
  const salvo = localStorage.getItem(CHAVE_TEMA);
  const preferido =
    salvo || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  aplicarTema(preferido);
})();

themeToggle.addEventListener("click", () => {
  aplicarTema(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

// --- utilitarios --------------------------------------------------------
function escapeHtml(str) {
  const mapa = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(str).replace(/[&<>"']/g, (ch) => mapa[ch]);
}

function formatarTamanho(bytes) {
  if (bytes == null) return "";
  const unidades = ["B", "KB", "MB", "GB"];
  let i = 0;
  let valor = bytes;
  while (valor >= 1024 && i < unidades.length - 1) {
    valor /= 1024;
    i += 1;
  }
  return `${valor.toFixed(i === 0 ? 0 : 1)} ${unidades[i]}`;
}

// Estimativa de duracao (TAREFA-2): "~38 min" pra menos de 1h, "~2h15min"
// acima disso - documentos reais (1000+ paginas) passam de 1h com frequencia.
function formatarDuracao(segundos) {
  if (segundos == null) return null;
  const minutos = segundos / 60;
  if (minutos < 1) return "menos de 1 min";
  if (minutos < 60) return `~${Math.round(minutos)} min`;
  const horas = Math.floor(minutos / 60);
  const restoMin = Math.round(minutos % 60);
  return restoMin > 0 ? `~${horas}h${restoMin}min` : `~${horas}h`;
}

// --- motor ativo ----------------------------------------------------------
async function carregarMotor() {
  try {
    const resp = await fetch("/api/motor");
    if (!resp.ok) throw new Error(String(resp.status));
    const dados = await resp.json();
    motorPill.textContent = `Motor: ${dados.engine}`;
  } catch (e) {
    motorPill.textContent = "Motor: indisponível";
  }
}

// A versao vem de /api/health (pdf_to_md.__version__, a mesma fonte que
// pyproject.toml le via [tool.setuptools.dynamic]) em vez de fixa no HTML -
// evita o subtitulo dessincronizar do pacote de verdade rodando no servidor.
async function carregarVersao() {
  try {
    const resp = await fetch("/api/health");
    if (!resp.ok) throw new Error(String(resp.status));
    const dados = await resp.json();
    appVersionEl.textContent = `v${dados.version}`;
  } catch (e) {
    appVersionEl.textContent = "";
  }
}

// --- upload ---------------------------------------------------------------
function modoOcrSelecionado() {
  const marcado = document.querySelector('input[name="modo-ocr"]:checked');
  return marcado ? marcado.value : "automatico";
}

async function enviarArquivos(files) {
  if (!files.length) return;
  const formData = new FormData();
  for (const arquivo of files) formData.append("files", arquivo);
  formData.append("modo_ocr", modoOcrSelecionado());

  try {
    const resp = await fetch("/api/jobs", { method: "POST", body: formData });
    if (!resp.ok) throw new Error(`upload falhou (${resp.status})`);
    const corpo = await resp.json();
    if (corpo.rejeitados && corpo.rejeitados.length) {
      const nomes = corpo.rejeitados.map((r) => r.nome_original).join(", ");
      window.alert(`Ignorado (não é PDF): ${nomes}`);
    }
  } catch (e) {
    window.alert(`Falha ao enviar arquivos: ${e.message}`);
  } finally {
    await atualizarFila();
  }
}

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("arrastando");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("arrastando");
  })
);
dropzone.addEventListener("drop", (e) => {
  const files = Array.from(e.dataTransfer?.files || []);
  enviarArquivos(files);
});
dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});
fileInput.addEventListener("change", () => {
  enviarArquivos(Array.from(fileInput.files || []));
  fileInput.value = "";
});

downloadAllBtn.addEventListener("click", (e) => {
  if (downloadAllBtn.getAttribute("aria-disabled") === "true") e.preventDefault();
});

// --- remocao de jobs --------------------------------------------------------
async function removerJob(jobId) {
  try {
    const resp = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (!resp.ok && resp.status !== 404) throw new Error(String(resp.status));
  } catch (e) {
    window.alert(`Falha ao remover: ${e.message}`);
  } finally {
    await atualizarFila();
  }
}

clearDoneBtn.addEventListener("click", async () => {
  clearDoneBtn.disabled = true;
  try {
    await fetch("/api/jobs", { method: "DELETE" });
  } finally {
    await atualizarFila();
  }
});

jobsList.addEventListener("click", (e) => {
  const botao = e.target.closest(".acao-remover");
  if (botao) removerJob(botao.dataset.jobId);
});

// --- icones (SVG inline, mesmo estilo em toda a lista) ---------------------
const ICONE_PDF =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"></path><path d="M14 3v4h4"></path></svg>';
const ICONE_RELOGIO =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"></circle><path d="M12 7.5V12l3 2"></path></svg>';
const ICONE_CHECK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"></circle><path d="M8.5 12.3l2.4 2.4 4.6-5.2"></path></svg>';
const ICONE_ALERTA =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"></circle><path d="M12 8v4.5"></path><path d="M12 16h.01"></path></svg>';
const ICONE_DOWNLOAD =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11"></path><path d="M8 12l4 4 4-4"></path><path d="M5 19.5h14"></path></svg>';
const ICONE_LIXEIRA =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7h14"></path><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path><path d="M7 7l1 13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-13"></path></svg>';

// --- renderizacao da fila ---------------------------------------------------
// "OCR: sim (detectado)" / "OCR: não (forçado)" - a decisao efetiva e sua
// origem, auditavel quando o resultado sair pior que o esperado (TAREFA-3).
// "desconhecido" (rodada 5, TAREFA-4): job retomado de uma execucao
// anterior apos um restart - a decisao de OCR original nao ficou gravada
// em lugar nenhum, entao o rotulo e suprimido em vez de mostrar um valor
// inventado como se fosse fato.
function rotuloOcr(job) {
  if (job.ocr_origem == null || job.ocr_origem === "desconhecido") return null;
  const origem = job.ocr_origem === "forcado" ? "forçado" : "detectado";
  return `OCR: ${job.ocr ? "sim" : "não"} (${origem})`;
}

// Grau de confianca da conversao (rodada 5, TAREFA-3) - so os GRAUS, nunca
// os escores numericos: mesmo criterio do backend (ver docs/backend.md).
// null quando o motor ativo nao expoe essa nocao (ex.: motor simples).
const RUBRICA_GRAU = { poor: "ruim", fair: "razoável", good: "boa", excellent: "excelente" };

function rotuloConfianca(job) {
  if (job.grau_medio == null) return null;
  return `confiança: ${RUBRICA_GRAU[job.grau_medio] || job.grau_medio}`;
}

function metaLinha(job) {
  const partes = [];
  if (job.paginas_totais != null) {
    partes.push(`${job.paginas_totais} página${job.paginas_totais === 1 ? "" : "s"}`);
  }
  if (job.tamanho_bytes) partes.push(formatarTamanho(job.tamanho_bytes));
  const ocr = rotuloOcr(job);
  if (ocr) partes.push(ocr);
  const confianca = rotuloConfianca(job);
  if (confianca) partes.push(confianca);
  return partes.join(" · ");
}

// "Confiança baixa nas páginas: X, Y" - so quando ha pelo menos uma pagina
// com grau ruim/razoavel (rodada 5, TAREFA-3). NAO e erro: a conversao
// segue utilizavel, e so um sinal de que pode valer revisao manual.
function detalheGrauBaixo(job) {
  if (!job.paginas_grau_baixo || job.paginas_grau_baixo.length === 0) return "";
  const paginas = job.paginas_grau_baixo.join(", ");
  return `<div class="meta-fina meta-aviso" title="Estas páginas ficaram com confiança baixa na conversão - o resultado segue utilizável, mas pode valer revisão manual.">Confiança baixa: página${job.paginas_grau_baixo.length === 1 ? "" : "s"} ${paginas}</div>`;
}

// Sufixo neutro de duracao estimada ("· ~38 min", opcionalmente marcado
// como estimativa inicial enquanto a EMA nao convergiu) - anexado a linha
// de na_fila/processando. Sempre exibido quando paginas_totais e conhecido,
// nao so acima do limiar do banner (esse so controla o AVISO, nao a
// informacao neutra em si).
function sufixoEstimativa(job) {
  const duracao = formatarDuracao(job.estimativa_segundos);
  if (duracao == null) return "";
  const marca = job.estimativa_baixa_confianca ? " (estimativa inicial)" : "";
  return ` · ${duracao}${marca}`;
}

function blocoEstado(job, totalNaFila) {
  if (job.status === "na_fila") {
    return `
      <div class="badge badge-amber">${ICONE_RELOGIO} Na fila</div>
      <div class="meta-fina">posição ${job.posicao_na_fila} de ${totalNaFila} na fila${sufixoEstimativa(job)}</div>
    `;
  }
  if (job.status === "processando") {
    const temTotal = job.paginas_totais != null && job.paginas_totais > 0;
    const pct = temTotal
      ? Math.min(100, Math.round((job.pagina_estimada / job.paginas_totais) * 100))
      : null;
    const barra =
      pct == null
        ? `<div class="progresso progresso-indeterminado"><div class="progresso-fill"></div></div>`
        : `<div class="progresso"><div class="progresso-fill" style="width:${pct}%"></div></div>`;
    const detalhe = temTotal
      ? `página ~${Math.min(job.paginas_totais, Math.round(job.pagina_estimada))}/${job.paginas_totais}`
      : "processando";
    return `
      <div class="badge badge-blue"><span class="spinner"></span> Processando</div>
      ${barra}
      <div class="meta-fina">${detalhe}${sufixoEstimativa(job)}</div>
    `;
  }
  if (job.status === "concluido") {
    return `
      <div class="badge badge-green">${ICONE_CHECK} Concluído</div>
      ${detalheGrauBaixo(job)}
    `;
  }
  return `
    <div class="badge badge-red">${ICONE_ALERTA} Erro</div>
    <div class="meta-fina meta-erro" title="${escapeHtml(job.mensagem_erro || "")}">${escapeHtml(job.mensagem_erro || "Falha na conversão")}</div>
  `;
}

function linhaJob(job, totalNaFila) {
  const nome = escapeHtml(job.nome_original);
  const baixar =
    job.status === "concluido"
      ? `<a class="acao-btn" href="/api/jobs/${job.id}/download" title="Baixar ${nome}">${ICONE_DOWNLOAD}</a>`
      : "";
  const remover =
    job.status === "processando"
      ? ""
      : `<button class="acao-btn acao-btn-perigo acao-remover" type="button" data-job-id="${job.id}" title="Remover ${nome}">${ICONE_LIXEIRA}</button>`;
  return `
    <div class="linha-job" data-status="${job.status}">
      <div class="icone-arquivo">${ICONE_PDF}</div>
      <div class="info-arquivo">
        <div class="nome-arquivo">${nome}</div>
        <div class="meta-fina">${metaLinha(job)}</div>
      </div>
      <div class="bloco-estado">${blocoEstado(job, totalNaFila)}</div>
      <div class="acao-slot">${baixar}${remover}</div>
    </div>
  `;
}

function renderizarFila() {
  const contagens = { na_fila: 0, processando: 0, concluido: 0, erro: 0 };
  for (const j of jobsCache) contagens[j.status] = (contagens[j.status] || 0) + 1;

  summaryEl.textContent = jobsCache.length
    ? `${contagens.processando} processando · ${contagens.na_fila} na fila · ${contagens.concluido} concluídos · ${contagens.erro} com erro`
    : "nenhum arquivo enviado ainda";

  // `disabled` nao e atributo valido em <a> - o navegador o ignora, entao so
  // funcionava por o CSS aplicar pointer-events:none e o JS interceptar o
  // clique; leitores de tela continuavam anunciando o link como ativo.
  // aria-disabled + remover href (sem destino, nao ha o que navegar) e o
  // padrao acessivel para um link estilizado como botao desabilitavel.
  if (contagens.concluido > 0) {
    downloadAllBtn.setAttribute("aria-disabled", "false");
    downloadAllBtn.setAttribute("href", "/api/download-zip");
  } else {
    downloadAllBtn.setAttribute("aria-disabled", "true");
    downloadAllBtn.removeAttribute("href");
  }
  clearDoneBtn.disabled = contagens.concluido + contagens.erro === 0;

  // Banner de estimativa (TAREFA-2): so acima do limiar, pra nao virar ruido
  // quando quase todo documento real (1000+ paginas) o ultrapassa. A
  // informacao neutra ("~38 min") ja aparece sempre na linha do job -
  // isso aqui e so o AVISO, quando o pior caso da fila e excepcional.
  const limiarSegundos = avisoEstimativaMinutos * 60;
  const jobLento = jobsCache.find(
    (j) =>
      (j.status === "na_fila" || j.status === "processando") &&
      j.estimativa_segundos != null &&
      j.estimativa_segundos > limiarSegundos
  );
  if (jobLento) {
    estimativaAlertaEl.textContent =
      `"${jobLento.nome_original}" deve levar ${formatarDuracao(jobLento.estimativa_segundos)} ` +
      `(acima de ${avisoEstimativaMinutos} min) - considere rodar fora do horário de pico.`;
    estimativaAlertaEl.hidden = false;
  } else {
    estimativaAlertaEl.hidden = true;
  }

  // jobsList.innerHTML = ... recria a lista inteira a cada poll (1.5s), o que
  // destroi qualquer elemento focado dentro dela - quem navega ate um botao
  // de remover perde o foco no proximo ciclo, indefinidamente. Guarda o
  // data-job-id de quem estava focado e restaura o foco no botao equivalente
  // apos o re-render (o job pode ter mudado de posicao/estado na lista).
  const focoAtual = document.activeElement;
  const jobIdComFoco =
    focoAtual instanceof HTMLElement && jobsList.contains(focoAtual)
      ? focoAtual.dataset.jobId
      : null;

  jobsList.innerHTML = jobsCache.length
    ? jobsCache.map((j) => linhaJob(j, contagens.na_fila)).join("")
    : '<div class="fila-vazia">Nenhum PDF na fila ainda.</div>';

  if (jobIdComFoco) {
    const botaoEquivalente = jobsList.querySelector(
      `[data-job-id="${jobIdComFoco}"]`
    );
    botaoEquivalente?.focus();
  }
}

// Numero de sequencia: setInterval nao espera o poll anterior terminar, e
// enviarArquivos()/removerJob() tambem chamam atualizarFila() por fora do
// ciclo (para refletir a acao na hora). Com o processo ocupado, respostas
// atrasadas podem chegar fora de ordem - sem isso, uma resposta antiga que
// chegue depois de uma mais nova sobrescrevia jobsCache e fazia a fila
// "andar para tras" (ex.: um job Concluido voltar a aparecer Processando).
let seqAtual = 0;

// Sem isso, uma queda do servidor era invisivel: o catch de atualizarFila()
// era vazio e carregarMotor() so roda uma vez no load - se o pill falhasse
// ali, ficava "Motor: indisponível" pra sempre, e a UI seguia fazendo poll
// em silencio sem nenhum sinal pro usuario de que nada estava respondendo.
const LIMITE_FALHAS_PARA_ALERTA = 3;
let falhasConsecutivas = 0;

function marcarFalhaDePoll() {
  falhasConsecutivas += 1;
  if (falhasConsecutivas >= LIMITE_FALHAS_PARA_ALERTA) {
    conexaoAlertaEl.hidden = false;
  }
}

function marcarSucessoDePoll() {
  const estavaAlertando = falhasConsecutivas >= LIMITE_FALHAS_PARA_ALERTA;
  falhasConsecutivas = 0;
  conexaoAlertaEl.hidden = true;
  if (estavaAlertando) carregarMotor(); // o pill pode ter ficado preso em "indisponível"
}

async function atualizarFila() {
  const seq = ++seqAtual;
  try {
    const resp = await fetch("/api/jobs");
    if (seq !== seqAtual) return; // resposta obsoleta - nem falha nem sucesso
    if (!resp.ok) {
      marcarFalhaDePoll();
      return;
    }
    const dados = await resp.json();
    if (seq !== seqAtual) return;
    jobsCache = dados.jobs || [];
    if (dados.aviso_estimativa_minutos != null) {
      avisoEstimativaMinutos = dados.aviso_estimativa_minutos;
    }
    renderizarFila();
    marcarSucessoDePoll();
  } catch (e) {
    if (seq === seqAtual) marcarFalhaDePoll();
  }
}

carregarMotor();
carregarVersao();
(function agendar() {
  atualizarFila().finally(() => setTimeout(agendar, POLL_MS));
})();
