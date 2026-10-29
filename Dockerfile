FROM python:3-slim


ADD requirements.txt /requirements.txt

RUN pip install --upgrade pip && \
	pip install --no-cache-dir -r /requirements.txt

COPY ./app /app

WORKDIR /app
EXPOSE 5000
USER nobody

CMD ["gunicorn", "-b", "[::]:5000", "--workers", "4", "--timeout", "30", "wsgi:app"]
