# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy only dependency manifests first (for Docker layer caching)
COPY pyproject.toml uv.lock ./

# Install CPU-only torch first, then everything else
RUN uv sync --frozen --no-dev

# Add the virtual environment to the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Now copy the rest of the application code
COPY . .

# Expose the ports
EXPOSE 8000

# Run the application.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]