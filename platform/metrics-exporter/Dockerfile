FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir boto3

COPY *.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "exporter.py"]
