#!/usr/bin/env bash
set -e

# Cores para saída
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQ_FILE="${SCRIPT_DIR}/requirements.txt"
PYTHON_BIN="${VENV_DIR}/bin/python"

echo -e "${BLUE}=== [1/3] Criando ambiente virtual Python (.venv) ===${NC}"
echo -e "Diretório: ${VENV_DIR}"

# Se o venv existir mas o interpretador estiver quebrado, limpa
if [ -d "${VENV_DIR}" ] && ! "${PYTHON_BIN}" -c "import sys; exit(0)" 2>/dev/null; then
    echo -e "${YELLOW}Ambiente virtual existente inválido. Recriando...${NC}"
    rm -rf "${VENV_DIR}"
fi

if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    echo -e "${GREEN}Ambiente virtual criado com sucesso!${NC}"
else
    echo -e "${YELLOW}Ambiente virtual já existente e funcional.${NC}"
fi

echo -e "\n${BLUE}=== [2/3] Atualizando pip ===${NC}"
"${PYTHON_BIN}" -m pip install --upgrade pip

echo -e "\n${BLUE}=== [3/3] Instalando dependências a partir do requirements.txt ===${NC}"
if [ -f "${REQ_FILE}" ]; then
    "${PYTHON_BIN}" -m pip install -r "${REQ_FILE}"
    echo -e "${GREEN}Dependências instaladas com sucesso!${NC}"
else
    echo -e "${RED}Arquivo requirements.txt não encontrado em: ${REQ_FILE}${NC}"
    exit 1
fi

echo -e "\n${GREEN}=== Ambiente configurado com sucesso em /app! ===${NC}"
echo -e "Execute o backup com: ${YELLOW}./executar_backup.sh${NC}"
