FROM python:3.12-slim

# نصب Node.js و PHP در کنار پایتون تا بات‌های آپلودی با هر زبونی اجرا بشن
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg ca-certificates unzip git \
    php-cli php-curl php-mbstring php-xml php-zip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# پوشه‌ای که بات‌های آپلودی توش ذخیره میشن (روی Railway یه Volume بهش وصل کن تا با ری‌دیپلوی پاک نشه)
ENV BOTS_DIR=/data/bots
RUN mkdir -p /data/bots

CMD ["python3", "main.py"]
