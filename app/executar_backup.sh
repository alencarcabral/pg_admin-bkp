#!/usr/bin/env bash
set -e

# Cores para saída
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
SCRIPT_PY="${SCRIPT_DIR}/backup_bancos.py"

# Se o ambiente virtual não existir ou estiver quebrado, inicializa automaticamente
if [ ! -x "${PYTHON_BIN}" ] || ! "${PYTHON_BIN}" -c "import sys; exit(0)" 2>/dev/null; then
    echo -e "${YELLOW}Ambiente virtual não encontrado ou inválido. Inicializando...${NC}"
    bash "${SCRIPT_DIR}/criar_ambiente.sh"
fi

# Executa o backup de forma direta
echo -e "${BLUE}Executando backup diretamente via Python e pg_dump...${NC}\n"
exec "${PYTHON_BIN}" "${SCRIPT_PY}" "$@"
