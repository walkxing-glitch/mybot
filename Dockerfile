FROM python:3.11-slim

# Install uv
RUN pip install uv

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY mybot/ ./mybot/

# Install dependencies
RUN uv pip install --system .

# Default data directory
RUN mkdir -p /app/data

CMD ["python", "-m", "mybot", "cli"]
