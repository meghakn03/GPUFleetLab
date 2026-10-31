FROM node:22-alpine AS ui
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY --from=ui /app/frontend/dist frontend/dist
USER 65532:65532
EXPOSE 8017
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8017"]
