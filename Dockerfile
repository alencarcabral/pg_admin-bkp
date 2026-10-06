FROM dpage/pgadmin4:latest
USER root
RUN usermod -u 1000 pgadmin 
# RUN groupmod -g 1000 pgadmin 
RUN chown -R pgadmin /var/lib/pgadmin
USER pgadmin
