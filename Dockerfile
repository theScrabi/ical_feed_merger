FROM docker://docker.io/tiangolo/uwsgi-nginx-flask:python3.12

COPY ./app /app
