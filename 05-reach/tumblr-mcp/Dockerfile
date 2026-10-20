FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY server.py .
COPY run_server.py .

# Create config directory
RUN mkdir -p config

# Port (configurable via env)
ENV PORT=8080
EXPOSE 8080

# Run server
CMD ["python", "run_server.py"]
