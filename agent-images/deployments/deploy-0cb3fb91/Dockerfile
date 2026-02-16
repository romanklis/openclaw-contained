
FROM python:3.11-slim

LABEL deployment_id="deploy-0cb3fb91"
LABEL task_id="task-6da2bcc1"

WORKDIR /app


# Install Python dependencies
RUN pip install --no-cache-dir flask


# Copy application files
COPY app/ /app/

EXPOSE 5000

CMD ["python", "app.py"]