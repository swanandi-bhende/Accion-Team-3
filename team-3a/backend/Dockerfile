FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
# ABIs come from the committed exports/ bundle; compose mounts artifacts/ and
# deployments/ over the top for local development.
COPY exports ./exports
RUN mkdir -p /app/artifacts /app/deployments
EXPOSE 8000
CMD ["uvicorn", "backend.rpc_wrapper.main:app", "--host", "0.0.0.0", "--port", "8000"]
