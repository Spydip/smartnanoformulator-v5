FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SMARTNANO_HOST=0.0.0.0 SMARTNANO_DB=/tmp/smartnano.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
RUN chmod +x /app/start.sh
EXPOSE 10000
CMD ["/app/start.sh"]
