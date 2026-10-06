#!/usr/bin/env python3
"""
Script para automação de backup de bancos de dados PostgreSQL.
Lê as configurações de /app/bancos.yaml, executa o pg_dump diretamente,
registra logs de auditoria e permite testar a conexão com os bancos.
"""

import os
import sys
import shutil
import argparse
import subprocess
import time
import shlex
import re
import socket
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

try:
    import yaml
except ImportError:
    print("ERRO: O módulo 'pyyaml' não está instalado.")
    print("Para instalar, execute: pip install -r /app/requirements.txt")
    sys.exit(1)


# Cores ANSI para saída no terminal
class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def log_info(msg: str):
    print(f"{Colors.BLUE}[INFO]{Colors.RESET} {msg}")


def log_success(msg: str):
    print(f"{Colors.GREEN}[SUCESSO]{Colors.RESET} {msg}")


def log_warning(msg: str):
    print(f"{Colors.YELLOW}[AVISO]{Colors.RESET} {msg}")


def log_error(msg: str):
    print(f"{Colors.RED}[ERRO]{Colors.RESET} {msg}")


def format_size(size_bytes: int) -> str:
    """Formata tamanho de bytes para formato legível (KB, MB, GB)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def sanitize_yaml_content(content: str) -> str:
    """
    Garante que nomes de seções sem ':' na raiz do YAML sejam aceitos
    corretamente pelo parser YAML.
    """
    lines = content.splitlines()
    sanitized_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped and not line.startswith((' ', '\t', '#')) and not stripped.endswith(':'):
            if ':' not in stripped:
                sanitized_lines.append(f"{stripped}:")
                continue
        sanitized_lines.append(line)
        
    return "\n".join(sanitized_lines)


def load_config(file_path: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Path]:
    """
    Carrega o arquivo YAML de configuração na pasta da aplicação.
    """
    app_dir = Path(__file__).resolve().parent
    candidates = []
    
    if file_path:
        candidates.append(Path(file_path))
    else:
        candidates.extend([
            app_dir / "bancos.yaml",
            app_dir / "banco.yaml",
            Path("bancos.yaml"),
            Path("banco.yaml")
        ])

    target_path = None
    for cand in candidates:
        if cand.is_file():
            target_path = cand.resolve()
            break

    if not target_path:
        searched = ", ".join(str(c) for c in candidates)
        raise FileNotFoundError(f"Arquivo de configuração não encontrado. Locais verificados: {searched}")

    with open(target_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError:
        sanitized_text = sanitize_yaml_content(raw_text)
        try:
            data = yaml.safe_load(sanitized_text)
        except yaml.YAMLError as exc:
            raise ValueError(f"Falha ao interpretar arquivo YAML '{target_path}': {exc}")

    if not isinstance(data, dict):
        raise ValueError(f"Estrutura inválida em '{target_path}': esperava um dicionário YAML.")

    general_config: Dict[str, Any] = {}
    databases_config: Dict[str, Any] = {}

    if "databases" in data and isinstance(data["databases"], dict):
        databases_config = data["databases"]
        if "general" in data and isinstance(data["general"], dict):
            general_config = data["general"]
    else:
        for k, v in data.items():
            if k == "general" and isinstance(v, dict):
                general_config = v
            elif isinstance(v, dict):
                databases_config[k] = v

    if not databases_config:
        raise ValueError(f"Nenhum banco de dados configurado encontrado em '{target_path}'.")

    return databases_config, general_config, target_path


def parse_db_entry(db_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza os parâmetros de conexão e opções de cada banco.
    Identifica também o database group (padrão: nome da seção).
    """
    if not isinstance(config, dict):
        raise ValueError(f"Configuração do banco '{db_name}' não é um dicionário.")

    db_group = (
        config.get("DATABASE_GROUP") or 
        config.get("database_group") or 
        config.get("Database_group") or 
        config.get("DB_GROUP") or 
        config.get("group") or 
        config.get("GROUP") or 
        db_name
    )
    db_group_str = str(db_group).strip() if db_group else db_name

    host = (
        config.get("POSTGRES_HOSTNAME") or 
        config.get("POSTGRES_HOST") or 
        config.get("DATABASE_HOST") or 
        config.get("host") or 
        "localhost"
    )
    user = (
        config.get("POSTGRES_USER") or 
        config.get("DATABASE_USER") or 
        config.get("username") or 
        config.get("user") or 
        "postgres"
    )
    password = (
        config.get("POSTGRES_PASSWORD") or 
        config.get("DATABASE_PASSWORD") or 
        config.get("password") or 
        ""
    )
    dbname = (
        config.get("POSTGRES_DB") or 
        config.get("DATABASE_NAME") or 
        config.get("database_name") or 
        config.get("database") or 
        config.get("dbname") or 
        db_name
    )
    port = (
        config.get("POSTGRES_PORT") or 
        config.get("DATABASE_PORT") or 
        config.get("port") or 
        5432
    )

    extra_args = (
        config.get("Extra_args") or 
        config.get("extra_args") or 
        config.get("EXTRA_ARGS") or 
        ""
    )
    verbose_opt = (
        config.get("Verbose") or 
        config.get("verbose") or 
        config.get("VERBOSE") or 
        ""
    )

    return {
        "alias": db_name,
        "group": db_group_str,
        "host": str(host).strip(),
        "user": str(user).strip(),
        "password": str(password).strip(),
        "dbname": str(dbname).strip(),
        "port": str(port).strip(),
        "extra_args": extra_args,
        "verbose_opt": verbose_opt
    }


def find_pg_dump(custom_path: Optional[str] = None) -> str:
    """
    Localiza o binário do pg_dump para execução direta.
    """
    if custom_path:
        if shutil.which(custom_path) or (os.path.isfile(custom_path) and os.access(custom_path, os.X_OK)):
            return custom_path
        raise FileNotFoundError(f"pg_dump especificado não encontrado ou sem permissão de execução: {custom_path}")

    found = shutil.which("pg_dump")
    if found:
        return found

    known_paths = [
        "/usr/local/pgsql-18/pg_dump",
        "/usr/local/pgsql-17/pg_dump",
        "/usr/local/pgsql-16/pg_dump",
        "/usr/local/pgsql-15/pg_dump",
        "/usr/local/pgsql-14/pg_dump",
        "/usr/local/pgsql-13/pg_dump",
        "/usr/lib/postgresql/18/bin/pg_dump",
        "/usr/lib/postgresql/17/bin/pg_dump",
        "/usr/lib/postgresql/16/bin/pg_dump",
        "/usr/lib/postgresql/15/bin/pg_dump",
        "/usr/lib/postgresql/14/bin/pg_dump",
        "/usr/bin/pg_dump"
    ]
    for p in known_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    raise FileNotFoundError(
        "Executável 'pg_dump' não foi encontrado no sistema.\n"
        "Certifique-se de que o PostgreSQL client está instalado ou informe o caminho via --pg-dump-path."
    )


def find_psql(custom_path: Optional[str] = None) -> Optional[str]:
    """
    Localiza o binário do psql para teste de autenticação e conectividade SQL.
    """
    if custom_path:
        candidate = Path(custom_path).parent / "psql"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    found = shutil.which("psql")
    if found:
        return found

    known_paths = [
        "/usr/local/pgsql-18/psql",
        "/usr/local/pgsql-17/psql",
        "/usr/local/pgsql-16/psql",
        "/usr/local/pgsql-15/psql",
        "/usr/local/pgsql-14/psql",
        "/usr/local/pgsql-13/psql",
        "/usr/lib/postgresql/18/bin/psql",
        "/usr/lib/postgresql/17/bin/psql",
        "/usr/lib/postgresql/16/bin/psql",
        "/usr/lib/postgresql/15/bin/psql",
        "/usr/lib/postgresql/14/bin/psql",
        "/usr/bin/psql"
    ]
    for p in known_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


def test_single_connection(db_info: Dict[str, Any], psql_bin: Optional[str] = None, timeout: int = 5) -> Dict[str, Any]:
    """
    Testa a conectividade com um banco de dados em duas etapas:
      1. Conexão TCP via socket (teste de rede e porta) com medição de latência.
      2. Autenticação e execução de query SQL via psql.
    """
    alias = db_info["alias"]
    host = db_info["host"]
    user = db_info["user"]
    dbname = db_info["dbname"]
    password = db_info["password"]
    
    try:
        port = int(db_info["port"])
    except ValueError:
        port = 5432

    result = {
        "alias": alias,
        "endpoint": f"{user}@{host}:{port}/{dbname}",
        "tcp_ok": False,
        "latency_ms": None,
        "auth_ok": False,
        "server_version": None,
        "status": "FALHA",
        "error": None
    }

    # 1. Teste de Socket TCP
    start_tcp = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = (time.time() - start_tcp) * 1000.0
            result["tcp_ok"] = True
            result["latency_ms"] = round(latency, 1)
    except socket.timeout:
        result["error"] = f"Timeout ({timeout}s) ao alcançar {host}:{port}"
        return result
    except ConnectionRefusedError:
        result["error"] = f"Conexão recusada em {host}:{port} (Porta fechada ou serviço inativo)"
        return result
    except Exception as exc:
        result["error"] = f"Erro de rede em {host}:{port}: {exc}"
        return result

    # 2. Teste de Autenticação SQL via psql
    if psql_bin:
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        env["PGCONNECT_TIMEOUT"] = str(timeout)

        cmd = [
            psql_bin,
            "-h", host,
            "-p", str(port),
            "-U", user,
            "-d", dbname,
            "-c", "SELECT version();",
            "--no-align",
            "--tuples-only"
        ]

        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout + 2)
            if proc.returncode == 0:
                result["auth_ok"] = True
                result["status"] = "CONECTADO"
                raw_ver = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "OK"
                match = re.match(r"(PostgreSQL\s+[\d\.]+)", raw_ver)
                result["server_version"] = match.group(1) if match else raw_ver[:35]
            else:
                err_msg = proc.stderr.strip()
                err_clean = re.sub(r"^psql:\s*error:\s*", "", err_msg, flags=re.IGNORECASE)
                result["error"] = err_clean.splitlines()[0] if err_clean else f"Falha de autenticação (código {proc.returncode})"
        except subprocess.TimeoutExpired:
            result["error"] = f"Timeout ({timeout}s) na autenticação com o banco '{dbname}'"
        except Exception as exc:
            result["error"] = f"Erro ao executar psql: {exc}"
    else:
        # Se psql não estiver disponível, o teste TCP confirmou porta aberta
        result["status"] = "TCP_OK"
        result["server_version"] = "Porta aberta (psql indisponível)"

    return result


def test_all_connections(databases_config: Dict[str, Any], targets: List[str], psql_bin: Optional[str], timeout: int = 5) -> bool:
    """
    Executa testes de conexão para todos os bancos selecionados e exibe um relatório formatado.
    Retorna True se todos estiverem acessíveis, False se houver falhas.
    """
    print(f"\n{Colors.BOLD}{Colors.CYAN}=== Testando Conexões com os Bancos de Dados ==={Colors.RESET}\n")
    if psql_bin:
        log_info(f"Utilitário de verificação SQL: {Colors.BOLD}{psql_bin}{Colors.RESET}")
    else:
        log_warning("psql não encontrado. O teste verificará conectividade de rede TCP.")

    log_info(f"Timeout por conexão: {timeout}s")
    log_info(f"Total de bancos a verificar: {len(targets)}\n")

    results: List[Dict[str, Any]] = []

    for idx, db_name in enumerate(targets, start=1):
        try:
            db_info = parse_db_entry(db_name, databases_config[db_name])
            print(f"[{idx}/{len(targets)}] Testando {Colors.BOLD}{db_name}{Colors.RESET} ({db_info['user']}@{db_info['host']}:{db_info['port']}/{db_info['dbname']})...", end=" ", flush=True)
            
            res = test_single_connection(db_info, psql_bin=psql_bin, timeout=timeout)
            results.append(res)

            if res["status"] == "CONECTADO":
                lat_str = f"({res['latency_ms']}ms)" if res.get('latency_ms') else ""
                print(f"{Colors.GREEN}[CONECTADO]{Colors.RESET} {lat_str} - {res.get('server_version')}")
            elif res["status"] == "TCP_OK":
                lat_str = f"({res['latency_ms']}ms)" if res.get('latency_ms') else ""
                print(f"{Colors.YELLOW}[PORTA ABERTA]{Colors.RESET} {lat_str}")
            else:
                print(f"{Colors.RED}[FALHA]{Colors.RESET} - {res.get('error')}")

        except Exception as exc:
            print(f"{Colors.RED}[ERRO]{Colors.RESET} - {exc}")
            results.append({
                "alias": db_name,
                "status": "ERRO",
                "endpoint": "-",
                "latency_ms": None,
                "server_version": None,
                "error": str(exc)
            })

    # Tabela resumo de conectividade
    print(f"\n{Colors.BOLD}{Colors.CYAN}================ RESUMO DO TESTE DE CONECTIVIDADE ================{Colors.RESET}")
    header = f"{'BANCO':<16} | {'STATUS':<14} | {'LATÊNCIA':<10} | {'VERSÃO / DETALHE DO ERRO'}"
    print(f"{Colors.BOLD}{header}{Colors.RESET}")
    print("-" * 80)

    success_count = sum(1 for r in results if r["status"] in ("CONECTADO", "TCP_OK"))
    fail_count = len(results) - success_count

    for r in results:
        status_color = Colors.GREEN if r["status"] == "CONECTADO" else (Colors.YELLOW if r["status"] == "TCP_OK" else Colors.RED)
        status_str = f"{status_color}{r['status']:<14}{Colors.RESET}"
        lat_str = f"{r['latency_ms']} ms" if r.get("latency_ms") is not None else "-"
        detail = r.get("server_version") if r["status"] in ("CONECTADO", "TCP_OK") else (r.get("error") or "Erro desconhecido")
        if len(detail) > 45:
            detail = detail[:42] + "..."
        print(f"{r['alias']:<16} | {status_str} | {lat_str:<10} | {detail}")

    print("-" * 80)
    print(f"Total: {len(results)} | {Colors.GREEN}Conectados: {success_count}{Colors.RESET} | {Colors.RED}Falhas: {fail_count}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}==================================================================={Colors.RESET}\n")

    return fail_count == 0


def run_backup(
    db_info: Dict[str, Any],
    pg_dump_bin: str,
    output_dir: Path,
    dump_format: str = "c",
    blobs: bool = True,
    no_owner: bool = True,
    no_privileges: bool = True,
    cli_verbose: bool = False,
    dry_run: bool = False,
    date_format: str = "%Y-%m-%d_%H%M%S"
) -> Dict[str, Any]:
    """
    Executa o backup de um banco de dados diretamente via pg_dump.
    Salva o backup dentro da subpasta com o nome do database group e
    o formato do arquivo inicia com data e hora seguido do postgres_db.
    """
    alias = db_info["alias"]
    group = db_info.get("group") or alias
    dbname = db_info["dbname"]
    timestamp = datetime.now().strftime(date_format)

    raw_extra_args = db_info.get("extra_args")
    extra_arg_tokens: List[str] = []
    if raw_extra_args:
        if isinstance(raw_extra_args, list):
            extra_arg_tokens = [str(x) for x in raw_extra_args]
        elif isinstance(raw_extra_args, str):
            extra_arg_tokens = shlex.split(raw_extra_args)

    raw_verbose = db_info.get("verbose_opt")
    verbose_tokens: List[str] = []
    if raw_verbose:
        if isinstance(raw_verbose, str) and raw_verbose.strip():
            verbose_tokens = shlex.split(raw_verbose)
        elif raw_verbose is True:
            verbose_tokens = ["--verbose"]
    elif cli_verbose:
        verbose_tokens = ["--verbose"]

    combined_extra = " ".join(extra_arg_tokens)
    has_format_in_extra = bool(re.search(r'(--format\b|-F\b)', combined_extra))
    
    if has_format_in_extra:
        match_fmt = re.search(r'(?:--format[= ]|-F\s*)([a-zA-Z]+)', combined_extra)
        fmt_val = match_fmt.group(1).lower() if match_fmt else "c"
        ext = "dump" if fmt_val in ("c", "custom") else "sql"
    else:
        ext = "dump" if dump_format in ("c", "custom") else "sql"

    # Salva o backup dentro da pasta com o nome do database group
    group_dir = output_dir / group
    if not dry_run:
        group_dir.mkdir(parents=True, exist_ok=True)

    # Formato do arquivo: primeiro data e hora seguido do postgres_db
    filename = f"{timestamp}_{dbname}.{ext}"
    output_path = group_dir / filename

    cmd = [
        pg_dump_bin,
        "--host", db_info["host"],
        "--port", str(db_info["port"]),
        "--username", db_info["user"]
    ]

    if not has_format_in_extra:
        fmt_flag = "c" if dump_format in ("c", "custom") else ("p" if dump_format in ("p", "plain") else dump_format)
        cmd.append(f"--format={fmt_flag}")

    if extra_arg_tokens:
        cmd.extend(extra_arg_tokens)
    else:
        if blobs:
            cmd.append("--blobs")
        if no_owner:
            cmd.append("--no-owner")
        if no_privileges:
            cmd.append("--no-privileges")

    for vt in verbose_tokens:
        if vt not in cmd:
            cmd.append(vt)

    cmd.append(db_info["dbname"])

    result_info = {
        "alias": alias,
        "group": group,
        "database": db_info["dbname"],
        "host": f"{db_info['host']}:{db_info['port']}",
        "user": db_info["user"],
        "file": str(output_path),
        "status": "PENDENTE",
        "duration": 0.0,
        "size_bytes": 0,
        "error": None
    }

    if dry_run:
        cmd_str = " ".join(cmd)
        log_info(f"[DRY-RUN] Backup para '{alias}' (Grupo: '{group}') -> {output_path}")
        log_info(f"[DRY-RUN] Comando direto: {cmd_str}")
        result_info["status"] = "SIMULADO"
        return result_info

    start_time = time.time()
    env = os.environ.copy()
    if db_info["password"]:
        env["PGPASSWORD"] = db_info["password"]

    try:
        with open(output_path, "wb") as out_file:
            process = subprocess.run(
                cmd,
                env=env,
                stdout=out_file,
                stderr=subprocess.PIPE,
                text=False
            )

        duration = time.time() - start_time
        result_info["duration"] = duration

        if process.returncode != 0:
            stderr_msg = process.stderr.decode("utf-8", errors="replace").strip()
            result_info["status"] = "FALHA"
            result_info["error"] = stderr_msg or f"pg_dump falhou com código {process.returncode}"
            if output_path.exists() and output_path.stat().st_size == 0:
                output_path.unlink()
        else:
            file_size = output_path.stat().st_size
            result_info["status"] = "SUCESSO"
            result_info["size_bytes"] = file_size

    except Exception as exc:
        duration = time.time() - start_time
        result_info["duration"] = duration
        result_info["status"] = "ERRO"
        result_info["error"] = str(exc)
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink()

    return result_info


def save_audit_log(
    log_file_path: Path,
    start_dt: datetime,
    end_dt: datetime,
    results: List[Dict[str, Any]],
    pg_dump_bin: str,
    config_file: Path,
    output_dir: Path,
    dry_run: bool = False
) -> None:
    """
    Grava o resumo da execução em um arquivo de log formatado para auditoria.
    O arquivo é mantido em modo append para registro contínuo do histórico.
    """
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    total_duration = (end_dt - start_dt).total_seconds()
    success_count = sum(1 for r in results if r["status"] == "SUCESSO")
    fail_count = sum(1 for r in results if r["status"] in ("FALHA", "ERRO"))
    sim_count = sum(1 for r in results if r["status"] == "SIMULADO")
    mode_str = "SIMULAÇÃO (DRY-RUN)" if dry_run else "EXECUÇÃO REAL"

    lines: List[str] = []
    lines.append("=" * 90)
    lines.append("AUDITORIA DE EXECUÇÃO DE BACKUP POSTGRESQL")
    lines.append("=" * 90)
    lines.append(f"Data e Hora de Início : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Data e Hora de Término: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Duração Total         : {total_duration:.2f}s")
    lines.append(f"Modo de Execução      : {mode_str}")
    lines.append(f"Executável pg_dump    : {pg_dump_bin}")
    lines.append(f"Arquivo Configuração  : {config_file}")
    lines.append(f"Diretório de Destino  : {output_dir}")
    lines.append("-" * 90)
    lines.append(f"{'BANCO':<18} | {'STATUS':<10} | {'DURAÇÃO':<9} | {'TAMANHO':<10} | {'ARQUIVO / DETALHE'}")
    lines.append("-" * 90)

    errors_detail: List[Tuple[str, str]] = []

    for r in results:
        alias = r["alias"]
        status = r["status"]
        dur = f"{r['duration']:.2f}s" if r.get("duration") else "-"
        size = format_size(r["size_bytes"]) if r.get("size_bytes") else "-"
        detail = r["file"] if status in ("SUCESSO", "SIMULADO") else (r.get("error") or "Erro desconhecido")
        
        if status in ("FALHA", "ERRO") and r.get("error"):
            errors_detail.append((alias, str(r["error"])))

        if status in ("SUCESSO", "SIMULADO"):
            detail_col = detail
        else:
            detail_col = detail if len(detail) <= 65 else detail[:62] + "..."
        lines.append(f"{alias:<18} | {status:<10} | {dur:<9} | {size:<10} | {detail_col}")

    lines.append("-" * 90)
    lines.append(f"Total: {len(results)} | Sucessos: {success_count} | Falhas: {fail_count} | Simulados: {sim_count}")

    if errors_detail:
        lines.append("\nDETALHAMENTO DE ERROS:")
        for alias, err in errors_detail:
            lines.append(f"[{alias}] {err}")

    lines.append("=" * 90 + "\n\n")

    with open(log_file_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(
        description="Utilitário de Backup Automatizado para Bancos de Dados PostgreSQL via chamada direta do pg_dump.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-c", "--config",
        help="Caminho do arquivo YAML com a lista e credenciais dos bancos (padrão: /app/bancos.yaml)",
        default=None
    )
    parser.add_argument(
        "-o", "--output-dir",
        help="Diretório onde os arquivos de backup serão salvos (padrão: definido em bancos.yaml ou /bancos/backups)",
        default=None
    )
    parser.add_argument(
        "-l", "--log-file",
        help="Caminho do arquivo de log para auditoria (padrão: definido em bancos.yaml ou <output_dir>/execucao_backups.log)",
        default=None
    )
    parser.add_argument(
        "-F", "--format",
        choices=["custom", "plain", "c", "p"],
        default="custom",
        help="Formato do dump padrão se não especificado em Extra_args"
    )
    parser.add_argument(
        "--pg-dump-path",
        default=None,
        help="Caminho explícito para o executável do pg_dump"
    )
    parser.add_argument(
        "--only",
        help="Filtrar bancos específicos para backup, separados por vírgula (ex: --only pumba,mapi)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula a execução dos backups sem chamar o pg_dump de fato"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Habilita logs detalhados durante a execução do pg_dump"
    )
    parser.add_argument(
        "-t", "--test-connections", "--test", "--check",
        dest="test_mode",
        action="store_true",
        help="Testa a conectividade e autenticação com os bancos de dados sem executar backups"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Tempo limite (em segundos) para os testes de conexão de cada banco"
    )

    args = parser.parse_args()

    # 1. Carrega configuração YAML
    try:
        databases_config, general_config, config_file = load_config(args.config)
    except Exception as exc:
        log_error(f"Erro ao carregar configuração: {exc}")
        sys.exit(1)

    # Filtra bancos se solicitado
    targets = list(databases_config.keys())
    if args.only:
        allowed = [x.strip() for x in args.only.split(",") if x.strip()]
        targets = [t for t in targets if t in allowed]
        if not targets:
            log_error(f"Nenhum banco selecionado corresponde ao filtro --only: {args.only}")
            sys.exit(1)

    # 2. MODO DE TESTE DE CONEXÃO
    if args.test_mode:
        psql_bin = find_psql(args.pg_dump_path)
        all_ok = test_all_connections(
            databases_config=databases_config,
            targets=targets,
            psql_bin=psql_bin,
            timeout=args.timeout
        )
        sys.exit(0 if all_ok else 1)

    # 3. MODO DE BACKUP
    start_datetime = datetime.now()

    print(f"\n{Colors.BOLD}{Colors.CYAN}=== Gerenciador Direto de Backups PostgreSQL (/app) ==={Colors.RESET}")
    print(f"{Colors.BOLD}Início da Execução: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}{Colors.RESET}\n")
    log_info(f"Configuração carregada de: {Colors.BOLD}{config_file}{Colors.RESET}")

    # Localiza binário pg_dump
    try:
        pg_dump_bin = find_pg_dump(args.pg_dump_path)
        log_info(f"Executável pg_dump: {Colors.BOLD}{pg_dump_bin}{Colors.RESET}")
    except Exception as exc:
        log_error(f"Erro ao localizar pg_dump: {exc}")
        sys.exit(1)

    # Prepara diretório de saída dos backups
    output_dir_str = args.output_dir or general_config.get("path_backup") or "/bancos/backups"
    out_dir = Path(output_dir_str).resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as exc:
        local_fallback = Path(__file__).resolve().parent.parent / "backups"
        log_warning(f"Não foi possível criar '{out_dir}' ({exc}). Utilizando fallback: {local_fallback}")
        out_dir = local_fallback
        out_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"Diretório de destino: {Colors.BOLD}{out_dir}{Colors.RESET}")

    # Formato padrão de data e hora (customizável via general.date_format no YAML)
    date_format = general_config.get("date_format") or "%Y-%m-%d_%H%M%S"

    # Determina arquivo de log de auditoria
    if args.log_file:
        log_file_str = args.log_file
    elif args.output_dir:
        log_file_str = str(out_dir / "execucao_backups.log")
    else:
        log_file_str = (
            general_config.get("path_log") or 
            general_config.get("log_file") or 
            str(out_dir / "execucao_backups.log")
        )

    log_file_path = Path(log_file_str).resolve()
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        # Fallback para o diretório de destino se o caminho configurado não puder ser criado
        log_file_path = (out_dir / "execucao_backups.log").resolve()
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

    log_info(f"Arquivo de log de auditoria: {Colors.BOLD}{log_file_path}{Colors.RESET}")

    log_info(f"Bancos a serem processados ({len(targets)}): {', '.join(targets)}\n")

    results: List[Dict[str, Any]] = []
    
    for idx, db_name in enumerate(targets, start=1):
        print(f"{Colors.BOLD}[{idx}/{len(targets)}] Processando banco: {Colors.CYAN}{db_name}{Colors.RESET}")
        try:
            db_info = parse_db_entry(db_name, databases_config[db_name])
            log_info(f" -> Database Group: {Colors.BOLD}{db_info['group']}{Colors.RESET}")
            log_info(f" -> Conexão: {db_info['user']}@{db_info['host']}:{db_info['port']} / {db_info['dbname']}")
            if db_info.get("extra_args"):
                log_info(f" -> Extra_args: {db_info['extra_args']}")
            if db_info.get("verbose_opt"):
                log_info(f" -> Verbose: {db_info['verbose_opt']}")
            
            res = run_backup(
                db_info=db_info,
                pg_dump_bin=pg_dump_bin,
                output_dir=out_dir,
                dump_format=args.format,
                cli_verbose=args.verbose,
                dry_run=args.dry_run,
                date_format=date_format
            )
            results.append(res)

            if res["status"] == "SUCESSO":
                log_success(f"Backup concluído em {res['duration']:.2f}s | Tamanho: {format_size(res['size_bytes'])}")
                log_info(f"Arquivo gerado: {res['file']}")
            elif res["status"] == "SIMULADO":
                log_info("Backup simulado com sucesso.")
            else:
                log_error(f"Falha no backup de '{db_name}': {res.get('error')}")

        except Exception as exc:
            log_error(f"Erro inesperado ao processar '{db_name}': {exc}")
            results.append({
                "alias": db_name,
                "status": "ERRO",
                "duration": 0.0,
                "size_bytes": 0,
                "error": str(exc),
                "file": "-"
            })
        print("-" * 60)

    end_datetime = datetime.now()
    total_elapsed = (end_datetime - start_datetime).total_seconds()

    # Salva Log de Auditoria
    try:
        save_audit_log(
            log_file_path=log_file_path,
            start_dt=start_datetime,
            end_dt=end_datetime,
            results=results,
            pg_dump_bin=pg_dump_bin,
            config_file=config_file,
            output_dir=out_dir,
            dry_run=args.dry_run
        )
        log_success(f"Auditoria registrada com sucesso em: {Colors.BOLD}{log_file_path}{Colors.RESET}")
    except Exception as exc:
        log_error(f"Falha ao salvar log de auditoria em '{log_file_path}': {exc}")

    # Sumário Final em tela
    print(f"\n{Colors.BOLD}{Colors.CYAN}================ RESUMO DOS BACKUPS ================{Colors.RESET}")
    print(f"Início : {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Término: {end_datetime.strftime('%Y-%m-%d %H:%M:%S')} (Duração: {total_elapsed:.2f}s)")
    print("-" * 80)

    success_count = sum(1 for r in results if r["status"] == "SUCESSO")
    fail_count = sum(1 for r in results if r["status"] in ("FALHA", "ERRO"))
    sim_count = sum(1 for r in results if r["status"] == "SIMULADO")

    header = f"{'BANCO':<18} | {'STATUS':<10} | {'DURAÇÃO':<9} | {'TAMANHO':<10} | {'ARQUIVO / ERRO'}"
    print(f"{Colors.BOLD}{header}{Colors.RESET}")
    print("-" * 80)
    for r in results:
        status_color = Colors.GREEN if r["status"] == "SUCESSO" else (Colors.YELLOW if r["status"] == "SIMULADO" else Colors.RED)
        status_str = f"{status_color}{r['status']:<10}{Colors.RESET}"
        dur_str = f"{r['duration']:.2f}s" if r.get("duration") else "-"
        size_str = format_size(r["size_bytes"]) if r.get("size_bytes") else "-"
        detail = r["file"] if r["status"] in ("SUCESSO", "SIMULADO") else (r.get("error") or "Erro desconhecido")
        if len(detail) > 80 and r["status"] not in ("SUCESSO", "SIMULADO"):
            detail = detail[:77] + "..."
        print(f"{r['alias']:<18} | {status_str} | {dur_str:<9} | {size_str:<10} | {detail}")
    
    print("-" * 80)
    print(f"Total: {len(results)} | {Colors.GREEN}Sucessos: {success_count}{Colors.RESET} | {Colors.RED}Falhas: {fail_count}{Colors.RESET}" +
          (f" | {Colors.YELLOW}Simulados: {sim_count}{Colors.RESET}" if sim_count else ""))
    print(f"{Colors.BOLD}{Colors.CYAN}===================================================={Colors.RESET}\n")

    if fail_count > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
