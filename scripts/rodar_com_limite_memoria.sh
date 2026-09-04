#!/usr/bin/env bash
# Executa um comando (tipicamente `pdf_to_md.py` ou `uvicorn backend.src.app:app`)
# sob um teto de memoria IMPOSTO PELO KERNEL, via cgroup (systemd-run --scope),
# em vez do limiar de RSS checado periodicamente em Python usado na rodada 4.
#
# Por que isso e melhor que checar RSS em loop:
# - O salto de RSS observado na rodada 4 foi de ~21 GB para ~44 GB em ~40
#   segundos. Um loop de checagem em Python (rodando na mesma CPU disputada
#   pela conversao) pode simplesmente nao rodar a tempo de reagir dentro
#   dessa janela.
# - Se o limiar do Python chegar tarde demais e a maquina bater no limite
#   fisico de RAM, quem escolhe a vitima do OOM killer e o kernel - numa
#   maquina compartilhada, pode nao ser o processo de conversao que morre.
# - MemoryMax de um cgroup e aplicado pelo kernel de forma deterministica: o
#   processo (e toda a sua arvore) e derrubado no instante em que ultrapassa
#   o teto, sem depender de nenhum codigo Python estar de olho nisso.
#
# Achado desta rodada (rodada 6, TAREFA-6): MemoryMax sozinho NAO basta.
# Com swap disponivel no host, o kernel prefere paginar para o swap a matar o
# cgroup ao bater no teto - testado empiricamente: MemoryMax=200M sem
# MemorySwapMax deixou um processo alocar 500 MB sem ser interrompido.
# Adicionando MemorySwapMax=0 (proibe esse cgroup de usar swap) o mesmo teste
# foi morto (SIGKILL, exit 137) assim que excedeu o limite. Por isso este
# script sempre define os dois.
#
# Uso:
#   scripts/rodar_com_limite_memoria.sh [-m TETO] -- comando [args...]
#
#   -m TETO   Valor de MemoryMax (formato do systemd: "24G", "24576M", etc).
#             Padrao: ver MEMORIA_PADRAO abaixo.
#
# Exemplos:
#   scripts/rodar_com_limite_memoria.sh -- .venv/bin/python3 pdf_to_md.py -i doc.pdf
#   scripts/rodar_com_limite_memoria.sh -m 20G -- .venv/bin/python3 pdf_to_md.py -i doc.pdf --no-ocr
#   HOST=127.0.0.1 scripts/rodar_com_limite_memoria.sh -m 40G -- scripts/start.sh
#
# Nao roda como root nem exige sudo: usa `systemd-run --user --scope`, que
# funciona sob o barramento de sessao do proprio usuario (systemd 255+;
# testado nesta maquina). Se `systemctl --user status` falhar (sem sessao de
# usuario, ex.: alguns containers), use `--scope` sem `--user` como root, ou
# rode dentro de uma unit de systemd de verdade.
#
# Valor recomendado (rodada 6, Bloco A - ver docs/architecture.md para a
# serie completa): picos observados nesta maquina de 62 GiB, com
# threads=4 (padrao), foram de ~44-48 GB num extrato de 46 paginas com OCR;
# com threads>4 (8/16/32/64) o pico salta para ~58 GB, um degrau, nao um
# gradiente. Uma faixa isolada de conteudo pesado chegou a ultrapassar 51 GB
# e ainda subia quando foi interrompida manualmente. O pico varia por
# CONTEUDO, nao so por tamanho do documento - o teto abaixo tem margem sobre
# o pico mais alto medido, nao sobre a media, mas nao e garantia absoluta
# contra um documento ainda mais pesado que os testados aqui.
set -euo pipefail

MEMORIA_PADRAO="55G"
TETO="$MEMORIA_PADRAO"

while getopts "m:" opt; do
    case "$opt" in
        m) TETO="$OPTARG" ;;
        *) echo "Uso: $0 [-m TETO] -- comando [args...]" >&2; exit 2 ;;
    esac
done
shift $((OPTIND - 1))

if [ "${1:-}" = "--" ]; then
    shift
fi

if [ $# -eq 0 ]; then
    echo "Uso: $0 [-m TETO] -- comando [args...]" >&2
    exit 2
fi

echo "Limite de memoria: MemoryMax=$TETO, MemorySwapMax=0 (sem fallback pra swap)" >&2
exec systemd-run --user --scope \
    -p "MemoryMax=$TETO" \
    -p "MemorySwapMax=0" \
    -- "$@"
