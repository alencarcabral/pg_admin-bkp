sudo chown -R 5050:5050 ./data


O erro ocorreu porque a imagem do pgAdmin 4 (dpage/pgadmin4) roda internamente com o usuário não-root pgadmin de UID 5050 e GID 5050.

Quando o volume local ./data foi montado em /var/lib/pgadmin, o diretório no host pertencia ao seu usuário do sistema (UID 1000) com permissões restritas a outros usuários (r-x). Como resultado, o pgAdmin não tinha permissão para criar as pastas internas como /var/lib/pgadmin/sessions.


As pastas internas (sessions/, storage/, azurecredentialcache/) e o banco de dados pgadmin4.db foram criados com sucesso.
O serviço está ativo e respondendo na porta 5050:
URL: http://localhost:5050
Usuário: admin@admin.com
Senha: admin



# 1. Testar / Simular sem gerar arquivos (dry-run):
docker exec -it pg_admin /app/executar_backup.sh --dry-run

# 2. Executar o backup real de todos os bancos:
docker exec -it pg_admin /app/executar_backup.sh

# 3. Executar apenas bancos específicos (ex: simmam):
docker exec -it pg_admin /app/executar_backup.sh --only simmam

# 4. Agendamento diário via CRON (todos os dias às 23:01):
# Abra o crontab do seu usuário com: crontab -e
# Adicione a seguinte linha (atenção: não use -it no cron):
1 23 * * * /usr/bin/docker exec pg_admin /app/executar_backup.sh >> /data_2tb/src_antigravity/pg_admin/backups/cron_exec.log 2>&1



# 1. Testar todos os bancos de dados configurados:
./testar_conexoes.sh

# 2. Testar apenas um banco específico (ex: pumba):
./testar_conexoes.sh --only pumba

# 3. Ajustar o tempo limite de timeout (ex: 3 segundos):
./testar_conexoes.sh --timeout 3



cd /app

# Simular a execução (dry-run):
./executar_backup.sh --dry-run

# Executar o backup de fato:
./executar_backup.sh

# Executar apenas bancos específicos:
./executar_backup.sh --only pumba,mapi

