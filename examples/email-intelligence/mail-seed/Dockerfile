# One-shot mail seeder for the email-intelligence mirror.
FROM python:3.11-slim

RUN pip install --no-cache-dir "reportlab==4.2.5"

WORKDIR /seed
COPY seed.py .

CMD ["python", "seed.py"]
