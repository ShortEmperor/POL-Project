FROM python:3.12-slim

# Usuario no-root por seguridad
RUN useradd --create-home --uid 1000 poluser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R poluser:poluser /app
USER poluser

EXPOSE 8050

# --workers 1 es OBLIGATORIO: el estado vive en memoria del proceso.
# Multiples workers tendrian cada uno su propia SimState.
# --threads 4 permite atender varias peticiones del mismo estado compartido.
CMD ["gunicorn", "--workers", "1", "--threads", "4", \
     "--bind", "0.0.0.0:8050", "app.main:server"]
