FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED True
WORKDIR /app

ADD requirements.txt .
RUN python3 -m pip install --upgrade pip \
    && pip3 --disable-pip-version-check --no-cache-dir install -r requirements.txt \
    && pip install --no-cache-dir gunicorn
RUN groupadd -r app && useradd -r -g app app && chown -R app:app /app

COPY --chown=app:app . .
USER app

CMD exec gunicorn --bind :$PORT --log-level info --workers 1 --threads 8 --timeout 0 app:server
