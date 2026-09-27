FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY proxy_client.py ./
COPY server_factory.py ./
COPY tool_specs.py ./
COPY run_server.py ./

ENV MCP_TRANSPORT=streamable-http
ENV HOST=0.0.0.0
ENV PORT=8080
ENV MACHINE_AGENT_SERVER=__SERVER_NAME__
ENV MACHINE_AGENT_BASE_URL=__MACHINE_AGENT_BASE_URL__
ENV MACHINE_AGENT_API_KEY=__MACHINE_AGENT_API_KEY__

EXPOSE 8080

CMD ["python", "run_server.py"]
