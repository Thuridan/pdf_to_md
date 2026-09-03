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

let jobsCache = [];

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
async function enviarArquivos(files) {
  if (!files.length) return;
  const formData = new FormData();
  for (const arquivo of files) formData.append("files", arquivo);

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
  if (downloadAllBtn.hasAttribute("disabled")) e.preventDefault();
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
function metaLinha(job) {
  const partes = [];
  if (job.paginas_totais != null) {
    partes.push(`${job.paginas_totais} página${job.paginas_totais === 1 ? "" : "s"}`);
  }
  if (job.tamanho_bytes) partes.push(formatarTamanho(job.tamanho_bytes));
  return partes.join(" · ");
}

function blocoEstado(job, totalNaFila) {
  if (job.status === "na_fila") {
    return `
      <div class="badge badge-amber">${ICONE_RELOGIO} Aguardando GPU</div>
      <div class="meta-fina">posição ${job.posicao_na_fila} de ${totalNaFila} na fila</div>
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
      <div class="meta-fina">${detalhe}</div>
    `;
  }
  if (job.status === "concluido") {
    return `<div class="badge badge-green">${ICONE_CHECK} Concluído</div>`;
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
    ? `${contagens.processando} processando · ${contagens.na_fila} aguardando GPU · ${contagens.concluido} concluídos · ${contagens.erro} com erro`
    : "nenhum arquivo enviado ainda";

  if (contagens.concluido > 0) {
    downloadAllBtn.removeAttribute("disabled");
  } else {
    downloadAllBtn.setAttribute("disabled", "");
  }
  clearDoneBtn.disabled = contagens.concluido + contagens.erro === 0;

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

async function atualizarFila() {
  const seq = ++seqAtual;
  try {
    const resp = await fetch("/api/jobs");
    if (!resp.ok || seq !== seqAtual) return;
    const dados = await resp.json();
    if (seq !== seqAtual) return;
    jobsCache = dados.jobs || [];
    renderizarFila();
  } catch (e) {
    // poll seguinte corrige - nao interrompe o ciclo por uma falha isolada
  }
}

carregarMotor();
carregarVersao();
(function agendar() {
  atualizarFila().finally(() => setTimeout(agendar, POLL_MS));
})();
