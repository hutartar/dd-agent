FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/mpl

WORKDIR /app

# 中文字体（图表里的中文）+ matplotlib 运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt && pip install "gunicorn>=21.2"

COPY . .

# 运行时目录（docker-compose 会挂载覆盖）
RUN mkdir -p web/results web/uploads output data

EXPOSE 5000

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "300", "--access-logfile", "-", "wsgi:app"]
