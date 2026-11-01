# Any Docker host: Railway, Fly.io, Hugging Face Spaces (Docker).
# Behind HTTPS it serves passkeys (Face ID / Touch ID / Windows Hello) to phones and laptops.
FROM python:3.13-slim
# PUBLIC_DEMO=1 below: pass a real SIGNING_SECRET when you run this image, or the server refuses to start.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PUBLIC_DEMO=1 PORT=8000 PAYPAL_MODE=simulator
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY scripts ./scripts
RUN mkdir -p data && useradd --create-home star && chown -R star data
USER star
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers"]
