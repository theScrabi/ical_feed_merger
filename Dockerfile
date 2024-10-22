FROM python:3.9


ADD requirements.txt /requirements.txt

RUN pip install --upgrade pip && \
	pip install --no-cache-dir -r /requirements.txt

COPY ./app /app

WORKDIR /app
EXPOSE 5000
USER nobody

CMD ["gunicorn", "-b", "0.0.0.0:5000", "wsgi:app"]
