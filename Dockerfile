FROM python:3.12-slim
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
WORKDIR /home/app/selftbc
COPY --chown=app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app selftbc ./selftbc
USER app
ENV SELFTBC_DB=/data/selftbc.db PORT=7860
EXPOSE 7860
CMD ["python", "-m", "selftbc"]
