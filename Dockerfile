FROM python:3.11-slim
WORKDIR /app

# Install dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
EXPOSE 8002

CMD ["python", "-X", "utf8", "manage.py", "runserver", "0.0.0.0:8002"]
