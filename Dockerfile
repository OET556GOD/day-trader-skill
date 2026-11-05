FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
# One worker on purpose: live calls are held in memory and the scheduler must run once.
CMD gunicorn --workers 1 --threads 16 --timeout 60 --bind 0.0.0.0:$PORT 'assistant.main:app'
