# An image is a stacked filesystem plus a start command. A container is a
# running process made from that image.

# Stage 1 installs dependencies. Compilers and the pip cache stay in this stage.
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Stage 2 is the runtime. It copies the installed packages only, so build tools
# never ship. The process runs as a non-root user.
FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
USER appuser
EXPOSE 8000
CMD ["uvicorn", "docpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
