FROM python:3.13-slim

# Create a non-root user
RUN useradd -m -u 1000 appuser

# Install ffmpeg
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set up working directory
WORKDIR /app

# Create and activate virtual environment
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set ownership
RUN chown -R appuser:appuser /app /opt/venv

# Switch to non-root user
USER appuser

# Install dependencies
COPY --chown=appuser:appuser pyproject.toml ./
RUN uv pip install -r pyproject.toml

# Copy the project into the image
COPY --chown=appuser:appuser . .

# Install the project
RUN uv pip install -e .

# Run the application
CMD ["python", "main.py"] 