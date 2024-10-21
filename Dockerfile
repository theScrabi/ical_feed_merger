FROM python:3.9

COPY ./app /app

ADD requirements.txt /app/requirements.txt

RUN pip install --upgrade pip && \
	pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "wsgi:app"]
