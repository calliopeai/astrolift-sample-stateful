FROM python:3.12-slim

# Build metadata surfaced by GET /debug (spec 42 §3.1). The workflow passes
# --build-arg GIT_SHA / BUILD_IMAGE / BUILT_AT; all default to dev-friendly
# values for a bare `docker build`.
ARG GIT_SHA=dev
ARG BUILD_IMAGE=""
ARG BUILT_AT=""
ENV APP_VERSION=${GIT_SHA} \
    BUILD_IMAGE=${BUILD_IMAGE} \
    BUILT_AT=${BUILT_AT} \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    STORE_PATH=/var/lib/store/data.db

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The platform renderer defaults to runAsNonRoot + readOnlyRootFilesystem.
# Run as an unprivileged UID; the ONLY writable path at runtime is the PVC
# mounted at /var/lib/store, which is where the SQLite DB lives. Create the
# mount point and hand it to the runtime user so a fresh PVC is writable.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /var/lib/store \
    && chown -R app:app /var/lib/store
USER 10001

EXPOSE 8080

# Shell form so ${PORT} expands; uvicorn binds the spec'd primary port.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
