# Proxy de salida del sandbox (Squid). Es la única ruta del agente hacia internet.
FROM alpine:3.24.3
RUN apk add --no-cache squid
COPY squid.conf /etc/squid/squid.conf
COPY permitidos_evaluacion.txt /etc/squid/permitidos.txt
EXPOSE 3128
USER squid
HEALTHCHECK --interval=5s --timeout=3s --retries=5 CMD nc -z 127.0.0.1 3128 || exit 1
ENTRYPOINT ["squid", "-N", "-d", "1", "-f", "/etc/squid/squid.conf"]
