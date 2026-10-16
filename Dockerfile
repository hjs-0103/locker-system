FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/data PORT=8000 HTTPS=1
VOLUME /data
EXPOSE 8000
CMD ["python", "run.py"]
