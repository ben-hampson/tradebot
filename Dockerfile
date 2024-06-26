FROM python:3.9-slim as python-base

    # Shows Python logs?
ENV PYTHONUNBUFFERED=1 \
    # prevents python creating .pyc files
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    # https://python-poetry.org/docs/configuration/#using-environment-variables
    # do not ask any interactive question
    POETRY_NO_INTERACTION=1 \
    # paths
    # this is where our requirements + virtual environment will live
    VENV_PATH="/venv"

# Prepend poetry and venv to path
ENV PATH="$VENV_PATH/bin:$PATH"

# `builder-base` stage is used to build deps + create our virtual environment
# build-essential for building python deps
FROM python-base as builder-base
RUN apt-get update --fix-missing \
    && apt-get install -y build-essential

RUN pip install --no-cache-dir poetry=="1.7.1"

# Copy project requirement files here to ensure they will be cached.
COPY pyproject.toml poetry.lock ./

# Install runtime deps - uses $POETRY_VIRTUALENVS_IN_PROJECT internally
RUN python -m venv "$VENV_PATH" \
    && . "$VENV_PATH/bin/activate" \
    && poetry install --no-root --no-dev

# `development` image is used during development / testing
FROM python-base as development

# Copy in our built poetry + venv
COPY --from=builder-base $VENV_PATH $VENV_PATH

# ================================================
ENV TZ=Europe/London

WORKDIR /home

RUN mkdir -p /home/data \
    && mkdir -p /home/logs

RUN apt-get -qq update -y && apt-get -qq install -y cron vim sqlite3
COPY root /etc/cron.d/root
RUN chmod 0644 /etc/cron.d/root
RUN crontab /etc/cron.d/root

COPY src/ ./src
COPY run/ ./run
COPY /start.sh .

CMD ["/bin/bash", "/home/start.sh"]
