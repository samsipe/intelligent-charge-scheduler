FROM python:3.11-slim-bookworm


COPY requirements.txt requirements.txt
RUN python3 -m pip install --upgrade pip \
    && pip3 --disable-pip-version-check --no-cache-dir install -r requirements.txt

WORKDIR /app
COPY . .
CMD ["python3", "-u", "scheduler.py", "-i", "14"]
