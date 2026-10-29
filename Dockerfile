FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python seed.py

EXPOSE 8000

CMD ["python", "main.py"]
