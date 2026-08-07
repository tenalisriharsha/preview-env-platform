FROM python:3.12-slim

WORKDIR /app
COPY main.py .

ENV PORT=8080
EXPOSE 8080

USER 65534:65534
CMD ["python", "main.py"]
