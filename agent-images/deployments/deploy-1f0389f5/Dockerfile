
FROM python:3.11-slim

LABEL deployment_id="deploy-1f0389f5"
LABEL task_id="task-1de48d12"

WORKDIR /app


# Install Python dependencies
RUN pip install --no-cache-dir flask


# Copy application files
COPY app/ /app/

EXPOSE 5000

CMD ["python3", "app.py"]